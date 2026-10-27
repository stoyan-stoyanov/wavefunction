"""Opt-in real DeepSeek orchestration check, using the product API and native sandbox.

Sends only synthetic source and application prompts/tool definitions through OpenRouter.
The task asks for parallel work but never names helpers or helper tools. This verifies
orchestration under that request, not how reliably the model chooses parallelism unprompted.
An observed-cost stop covers all parent/helper calls; in-flight requests can overshoot it.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import hashlib
import json
import os
import re
import shlex
import subprocess
import time
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx

TASK = """Please work on these three independent deliverables in parallel, as described in README.md:
1. Billing rules: tiered pricing, coupons and invoice tax in billing.py.
2. Usage analysis: date windows, event deduplication and top-model summaries in usage.py.
3. Data exchange: configurable CSV export/import with strict validation in exporting.py.
Their APIs and tests are already specified, so each module can be implemented independently.
Also wire the finished APIs together in report.py. Finish the complete outcome, preserve
the supplied tests and existing notes, and verify the combined report."""
ADDITION = """Also implement returns.py: refundable_cents(paid_cents, returned_units, total_units)
returns the proportional integer-cent refund, rounding half up. paid_cents and returned_units
must be nonnegative integers, total_units must be a positive integer, and returned_units must
not exceed total_units; invalid inputs raise ValueError. Include it in build_report as
refund_cents=0 by default, or the computed value when an optional return_request dict with
these three fields is supplied. Keep working on the original invoice/report requirements too."""
CORRECTIONS = {
    "billing": """Correction to the billing requirement while it is being implemented: the free
shipping threshold is 7500 cents after discount, not 5000. A nonempty 6000-cent order must
therefore pay 500 cents shipping; exactly 7500 is free. Please pass this correction to whoever
is implementing billing and incorporate it into the final report. All other requirements stand.""",
    "usage": """Correction to aggregate_daily while usage is being implemented: the date window
must be start-inclusive and end-exclusive. An event on the end date must be excluded, and
equal start/end dates mean an empty result. Please pass this correction to whoever is
implementing usage. All other requirements and the original report work still stand.""",
    "exporting": """Correction to the CSV requirements while exporting is being implemented:
support semicolon ';' as an additional delimiter in both render_csv and parse_csv, alongside
comma and tab. Preserve quoting and column validation. Please pass this correction to whoever
is implementing exporting. All other requirements and the original report work still stand.""",
}

README = """# Synthetic invoice reporting

Implement the modules using only the Python standard library. Monetary values are
integer cents; booleans are not valid integers. No dependency installation or networking.

## billing.py: quote(items, discount_percent=0)
items is a list of {sku, price_cents, quantity}. Reject non-integer/negative prices or
quantities and discount percentages outside integer 0..100 with ValueError. Sum line totals.
Round the percentage discount half up to a cent. Shipping is 500 cents if the post-discount
subtotal is below 5000, except that an empty order or zero total quantity has no shipping.
Return {subtotal_cents, discount_cents, shipping_cents, total_cents}. Do not mutate inputs.

tiered_total(quantity, tiers): progressive per-unit prices. quantity is a nonnegative integer;
tiers is a nonempty list of {up_to, unit_cents}. up_to is an exclusive cumulative positive
integer capacity for each bounded tier, strictly increasing; only the final tier has
up_to=None and it is required. unit_cents is a nonnegative integer. Example quantity 7,
tiers [{up_to:5,unit_cents:100},{up_to:None,unit_cents:80}] costs 660. Validate all tiers even
for zero quantity; reject invalid values with ValueError, never mutate tiers.

coupon_discount(subtotal_cents, coupon): subtotal is a nonnegative integer. coupon is None
(zero discount), {kind:'percent',value:integer 0..100}, or {kind:'fixed',value:nonnegative
integer cents}; optional cap_cents is nonnegative integer. Round percent half up, cap to
cap_cents when provided, then to subtotal. Reject invalid kind/values with ValueError.

invoice(items, discount_percent=0, tax_percent=0, coupon=None): start with quote. tax_percent
is integer 0..100. Apply coupon_discount after quote's percentage discount (not to shipping),
then compute half-up tax on the remaining merchandise (not shipping). Preserve quote fields,
add coupon_cents and tax_cents, and adjust total_cents to include coupon and tax. Do not
recompute quote's shipping after the coupon. Preserve inputs and reject invalid tax.

## usage.py: summarize(events)
Each event contains model (nonempty string), input_tokens and output_tokens (nonnegative
integers), and optional cached_tokens (default 0, nonnegative integer <= input_tokens).
Reject invalid rows with ValueError. Aggregate into a list sorted by model, one row per
model with model, runs, input_tokens, output_tokens, cached_tokens, uncached_tokens, total_tokens.
Uncached is input minus cached; total is input plus output. Empty events produce [].
Do not mutate input rows. Models with commas/quotes are valid.

aggregate_daily(events, start, end): start/end are strict ISO YYYY-MM-DD dates (start <= end).
Each event additionally has id (nonempty string) and date (strict ISO date). Validate every
row before filtering, including out-of-window rows, and do not mutate inputs. Deduplicate
identical rows with the same ID (missing cached_tokens equals zero); the same ID with any
different model/date/token data raises ValueError. Include start and end dates. Return a
list sorted by date then model, with date added to each summarize-style row. Empty input
or no matching days produces []; do not synthesize zero days. Invalid dates raise ValueError.

top_models(rows, limit): nonnegative integer limit, reject bool/negative/non-integer limits.
Combine summarize-style rows across days/models, sort by descending total_tokens then model,
return first limit rows in the same summarize-style format without date. Do not mutate rows.

## exporting.py: render_csv(rows)
Return a CSV string with exactly this header, in this order:
model,runs,input_tokens,cached_tokens,uncached_tokens,output_tokens,total_tokens
Emit rows in the given order. Escape commas, quotes and newlines correctly, use LF row
endings, and always include the header (also for empty input). Do not mutate rows.

render_csv also accepts columns=None and delimiter=','. columns, if provided, is a nonempty
list of distinct known header fields in the requested order; reject invalid names/duplicates.
Support delimiter ',' or '\\t'; reject all other delimiters with ValueError. Validate numeric
fields as nonnegative integers (not bool), and model as nonempty string. Missing any field
requested by columns raises ValueError. CSV column selection may omit model.

parse_csv(text, delimiter=','): parse full rows back to typed dictionaries. Header order may
vary but every standard field must appear exactly once, with no extra names. Reject missing/
duplicate/unknown headers, ragged rows, empty model, negative/non-integer numeric fields,
cached_tokens > input_tokens, wrong uncached/total arithmetic, and malformed quoting with
ValueError. Input numeric cells may contain surrounding spaces, but only ASCII digits after
stripping. Header-only input returns []; preserve row order and correctly handle embedded
newlines, quotes and delimiters. Use the same supported delimiters as render_csv.

## report.py: build_report(items, events, discount_percent=0)
Return {quote: billing.quote(...), usage: usage.summarize(...), csv: exporting.render_csv(...)}.
This must call the implemented modules, not duplicate their logic.

Also expose build_invoice_report(items, events, start, end, discount_percent=0, tax_percent=0,
coupon=None, columns=None, delimiter=',', top_limit=5), returning invoice (billing.invoice),
daily_usage (usage.aggregate_daily), top_models (usage.top_models of daily_usage), and csv
(exporting.render_csv of top_models using columns/delimiter). Delegate to the module APIs.

Run python3 -m unittest -v. Existing tests are immutable reference fixtures; external
contract checks will also run against the final parent checkout.
"""
FIXTURES = {
    "README.md": README,
    "AGENTS.md": """Make focused source changes; preserve README.md, tests and notes.txt.
Use read_file/edit_file/write_file for source work. Shell use is limited to read-only Git
inspection and python3 -m unittest -v (optionally specific supplied test modules).
Do not install anything, start servers or terminals, execute deletion commands, or access
files outside this synthetic repository and any owned helper checkout. No cleanup commands
are needed: the verifier owns fixture cleanup. Use only the standard library.
""",
    "notes.txt": "Unrelated existing user note: keep this exactly.\n",
    "billing.py": "def quote(items, discount_percent=0):\n    raise NotImplementedError\n",
    "usage.py": "def summarize(events):\n    raise NotImplementedError\n",
    "exporting.py": "def render_csv(rows):\n    raise NotImplementedError\n",
    "report.py": "def build_report(items, events, discount_percent=0):\n    raise NotImplementedError\n",
    "test_billing.py": """import unittest
from billing import quote
class BillingTests(unittest.TestCase):
    def test_totals_and_rounding(self):
        self.assertEqual(quote([{'sku':'a','price_cents':101,'quantity':3}], 50),
            dict(subtotal_cents=303, discount_cents=152, shipping_cents=500, total_cents=651))
    def test_empty_and_free_shipping(self):
        self.assertEqual(quote([])['total_cents'], 0)
        self.assertEqual(quote([{'sku':'a','price_cents':10000,'quantity':1}])['shipping_cents'], 0)
    def test_validation(self):
        for value in (-1, 1.5, True):
            with self.assertRaises(ValueError):
                quote([{'sku':'a','price_cents':value,'quantity':1}])
        with self.assertRaises(ValueError): quote([], 101)
    def test_tiers(self):
        from billing import tiered_total
        tiers=[dict(up_to=5,unit_cents=100),dict(up_to=10,unit_cents=80),dict(up_to=None,unit_cents=50)]
        self.assertEqual([tiered_total(n,tiers) for n in (0,5,7,10,12)],[0,500,660,900,1000])
        for invalid in ([],[dict(up_to=5,unit_cents=1)],
                        [dict(up_to=5,unit_cents=1),dict(up_to=4,unit_cents=1),dict(up_to=None,unit_cents=1)],
                        [dict(up_to=None,unit_cents=-1)], [dict(up_to=None,unit_cents=True)]):
            with self.assertRaises(ValueError): tiered_total(0,invalid)
    def test_coupons_and_tax(self):
        from billing import coupon_discount, invoice
        self.assertEqual(coupon_discount(101,dict(kind='percent',value=50)),51)
        self.assertEqual(coupon_discount(101,dict(kind='fixed',value=500)),101)
        self.assertEqual(coupon_discount(1000,dict(kind='percent',value=50,cap_cents=100)),100)
        result=invoice([dict(sku='a',price_cents=10000,quantity=1)],10,7,dict(kind='fixed',value=500))
        self.assertEqual(result,dict(subtotal_cents=10000,discount_cents=1000,shipping_cents=0,
            coupon_cents=500,tax_cents=595,total_cents=9095))
        for coupon in (dict(kind='other',value=1),dict(kind='percent',value=101),dict(kind='fixed',value=True)):
            with self.assertRaises(ValueError): coupon_discount(100,coupon)
""",
    "test_usage.py": """import unittest
from usage import summarize
class UsageTests(unittest.TestCase):
    def test_group_and_sort(self):
        events=[dict(model='z',input_tokens=10,output_tokens=3,cached_tokens=6),
                dict(model='a',input_tokens=7,output_tokens=2),
                dict(model='z',input_tokens=5,output_tokens=1,cached_tokens=4)]
        rows=summarize(events)
        self.assertEqual([r['model'] for r in rows], ['a','z'])
        self.assertEqual(rows[1], dict(model='z',runs=2,input_tokens=15,output_tokens=4,
            cached_tokens=10,uncached_tokens=5,total_tokens=19))
        self.assertEqual(summarize([]), [])
    def test_validation(self):
        for row in (dict(model='a',input_tokens=1,output_tokens=0,cached_tokens=2),
                    dict(model='',input_tokens=1,output_tokens=0),
                    dict(model='a',input_tokens=True,output_tokens=0)):
            with self.assertRaises(ValueError): summarize([row])
    def test_daily_deduplication_and_validation(self):
        from usage import aggregate_daily
        row=dict(id='a',date='2026-10-02',model='m',input_tokens=10,output_tokens=2,cached_tokens=4)
        result=aggregate_daily([row,dict(row)],'2026-10-01','2026-10-04')
        self.assertEqual(result,[dict(date='2026-10-02',model='m',runs=1,input_tokens=10,
            output_tokens=2,cached_tokens=4,uncached_tokens=6,total_tokens=12)])
        with self.assertRaises(ValueError):
            aggregate_daily([row,dict(row,output_tokens=3)],'2026-10-01','2026-10-04')
        with self.assertRaises(ValueError):
            aggregate_daily([dict(row,date='2026-09-30',input_tokens=-1)],'2026-10-01','2026-10-04')
        with self.assertRaises(ValueError): aggregate_daily([], '2026-2-01','2026-10-04')
        with self.assertRaises(ValueError): aggregate_daily([], '2026-10-04','2026-10-01')
    def test_top_models(self):
        from usage import top_models
        rows=[dict(model='z',runs=1,input_tokens=10,output_tokens=2,cached_tokens=4,uncached_tokens=6,total_tokens=12),
              dict(model='a',runs=1,input_tokens=10,output_tokens=2,cached_tokens=4,uncached_tokens=6,total_tokens=12)]
        self.assertEqual([row['model'] for row in top_models(rows,2)],['a','z'])
        self.assertEqual(top_models(rows+rows,1)[0]['total_tokens'],24)
        self.assertEqual(top_models(rows,0),[])
        with self.assertRaises(ValueError): top_models(rows,True)
""",
    "test_exporting.py": """import csv
import io
import unittest
from exporting import render_csv
class ExportTests(unittest.TestCase):
    def test_header_and_escaping(self):
        header=['model','runs','input_tokens','cached_tokens','uncached_tokens','output_tokens','total_tokens']
        self.assertEqual(render_csv([]), ','.join(header)+'\\n')
        row=dict(zip(header,['a,"b"\\nc',1,10,4,6,2,12]))
        raw=render_csv([row])
        self.assertNotIn('\\r',raw)
        self.assertEqual(list(csv.reader(io.StringIO(raw))),[header,list(map(str,row.values()))])
    def test_columns_delimiter_and_roundtrip(self):
        from exporting import parse_csv
        row=dict(model='a,"b"\\nc',runs=2,input_tokens=10,cached_tokens=4,uncached_tokens=6,output_tokens=2,total_tokens=12)
        self.assertEqual(parse_csv(render_csv([row])),[row])
        self.assertEqual(parse_csv(render_csv([row],delimiter='\\t'),delimiter='\\t'),[row])
        self.assertEqual(render_csv([row],columns=['runs','total_tokens']),'runs,total_tokens\\n2,12\\n')
        for cols in ([],['runs','runs'],['unknown']):
            with self.assertRaises(ValueError): render_csv([row],columns=cols)
        with self.assertRaises(ValueError): render_csv([row],delimiter='|')
        with self.assertRaises(ValueError): render_csv([dict(row,runs=True)])
    def test_invalid_imports(self):
        from exporting import parse_csv
        header='model,runs,input_tokens,cached_tokens,uncached_tokens,output_tokens,total_tokens\\n'
        self.assertEqual(parse_csv(header),[])
        for body in ('a,1,10,11,-1,2,12\\n','a,1,10,4,5,2,12\\n','a,1,10,4,6,2,99\\n',
                     'a,1,10,4,6,2\\n',',1,10,4,6,2,12\\n','a,1,10.0,4,6,2,12\\n'):
            with self.assertRaises(ValueError): parse_csv(header+body)
        with self.assertRaises(ValueError): parse_csv('model,model\\na,b\\n')
""",
    "test_report.py": """import unittest
from report import build_report
class ReportTests(unittest.TestCase):
    def test_combined(self):
        value=build_report([dict(sku='a',price_cents=10000,quantity=1)],
            [dict(model='m',input_tokens=10,output_tokens=2,cached_tokens=8)],10)
        self.assertEqual(value['quote']['total_cents'],9000)
        self.assertEqual(value['usage'][0]['uncached_tokens'],2)
        self.assertIn('m,1,10,8,2,2,12\\n',value['csv'])
    def test_invoice_report(self):
        from report import build_invoice_report
        result=build_invoice_report([dict(sku='a',price_cents=10000,quantity=1)],
            [dict(id='a',date='2026-10-02',model='m',input_tokens=10,output_tokens=2,cached_tokens=8)],
            '2026-10-01','2026-10-04',discount_percent=10,tax_percent=5,
            coupon=dict(kind='fixed',value=1000),columns=['model','total_tokens'])
        self.assertEqual(result['invoice']['total_cents'],8400)
        self.assertEqual(result['daily_usage'][0]['date'],'2026-10-02')
        self.assertEqual(result['top_models'][0]['total_tokens'],12)
        self.assertEqual(result['csv'],'model,total_tokens\\nm,12\\n')
""",
}
SOURCE_NAMES = {"billing.py", "usage.py", "exporting.py", "report.py", "returns.py"}
IMPORTS = {
    "billing",
    "usage",
    "exporting",
    "report",
    "returns",
    "unittest",
    "csv",
    "io",
    "decimal",
    "math",
    "collections",
    "typing",
    "dataclasses",
    "copy",
    "datetime",
    "re",
}
FINAL_CHECKS = """import sys, unittest, copy
sys.path.insert(0, sys.argv[1])
from billing import quote
from usage import summarize
from returns import refundable_cents
from report import build_report
suite=unittest.defaultTestLoader.discover(sys.argv[1], pattern='test_*.py')
result=unittest.TextTestRunner(verbosity=2).run(suite)
assert result.wasSuccessful(), 'Supplied contract tests failed'
correction=sys.argv[2]
if correction=='billing':
    assert quote([dict(sku='x',price_cents=6000,quantity=1)])['shipping_cents']==500
    assert quote([dict(sku='x',price_cents=7500,quantity=1)])['shipping_cents']==0
    assert quote([dict(sku='x',price_cents=8000,quantity=1)],10)['shipping_cents']==500
elif correction=='usage':
    from usage import aggregate_daily
    rows=[dict(id='a',date='2026-10-02',model='m',input_tokens=1,output_tokens=0),
          dict(id='b',date='2026-10-03',model='m',input_tokens=2,output_tokens=0)]
    assert len(aggregate_daily(rows,'2026-10-02','2026-10-03'))==1
    assert aggregate_daily(rows,'2026-10-02','2026-10-02')==[]
elif correction=='exporting':
    from exporting import render_csv, parse_csv
    row=dict(model='a;b',runs=1,input_tokens=10,cached_tokens=4,uncached_tokens=6,output_tokens=2,total_tokens=12)
    assert parse_csv(render_csv([row],delimiter=';'),delimiter=';')==[row]
else: raise AssertionError('No live helper correction was delivered')
assert quote([dict(sku='x',price_cents=8000,quantity=0)])['shipping_cents']==0
assert refundable_cents(101,1,2)==51
assert refundable_cents(101,2,2)==101
assert refundable_cents(101,0,2)==0
for args in [(-1,1,2),(101,-1,2),(101,1,0),(101,3,2),(True,1,2),(101,1.5,2)]:
    try: refundable_cents(*args)
    except ValueError: pass
    else: raise AssertionError(('invalid refund accepted',args))
items=[dict(sku='x',price_cents=6000,quantity=1)]
events=[dict(model='z',input_tokens=9,output_tokens=2,cached_tokens=7)]
original=copy.deepcopy((items,events))
value=build_report(items,events,return_request=dict(paid_cents=101,returned_units=1,total_units=2))
assert value['refund_cents']==51 and value['quote']['total_cents']==(6500 if correction=='billing' else 6000)
assert build_report([],[])['refund_cents']==0
assert value['usage']==summarize(events) and (items,events)==original
print('Independent corrected-contract checks passed')
"""


def git(folder, *args):
    return subprocess.run(
        ["git", "-C", str(folder), *args], check=True, capture_output=True, text=True, timeout=30
    ).stdout.strip()


def inspect_python(folder):
    """Allow only the tiny pure-Python fixture domain before executing model-written code."""
    for path in folder.glob("*.py"):
        tree = ast.parse(path.read_text(), filename=path.name)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(
                item.name.split(".")[0] not in IMPORTS for item in node.names
            ):
                raise ValueError(f"Unexpected import in synthetic source: {path.name}")
            if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] not in IMPORTS:
                raise ValueError(f"Unexpected import in synthetic source: {path.name}")
            if isinstance(node, ast.Attribute) and (
                node.attr.startswith("__")
                or node.attr
                in {"remove", "unlink", "rmdir", "rmtree", "system", "popen", "open", "exec", "eval"}
            ):
                raise ValueError(f"Unexpected introspection in synthetic source: {path.name}")
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id
                in {
                    "open",
                    "eval",
                    "exec",
                    "compile",
                    "__import__",
                    "getattr",
                    "setattr",
                    "delattr",
                    "globals",
                    "locals",
                }
            ):
                raise ValueError(f"Unexpected dynamic/file operation in synthetic source: {path.name}")


def safe_command(command):
    if any(char in command for char in ("\n", "$", "`", ";", "|", "&", ">", "<")):
        return False
    parts = shlex.split(command)
    if parts[:3] in (["python3", "-m", "unittest"], ["python", "-m", "unittest"]):
        return all(
            item
            in {
                "-v",
                "-q",
                "test_billing",
                "test_usage",
                "test_exporting",
                "test_report",
                "test_billing.py",
                "test_usage.py",
                "test_exporting.py",
                "test_report.py",
            }
            for item in parts[3:]
        )
    if len(parts) >= 2 and parts[:2] in (
        ["git", "status"],
        ["git", "diff"],
        ["git", "show"],
        ["git", "log"],
        ["git", "rev-parse"],
    ):
        return not any(item.startswith(("--output", "--ext-diff", "--textconv")) for item in parts[2:])
    return False


async def verify(args):
    from langchain.agents.middleware import AgentMiddleware
    from langchain_core.messages import ToolMessage
    from wavefunction.secrets import EnvFileSecretStore, MemorySecretStore

    key = (
        EnvFileSecretStore(args.key_file.expanduser()).get("openrouter_api_key")
        if args.key_file
        else os.environ.get("OPENROUTER_API_KEY")
    )
    if not key:
        raise ValueError("Supply --key-file or OPENROUTER_API_KEY")
    ledger = (
        json.loads(args.cost_ledger.read_text())
        if args.cost_ledger.exists()
        else {"observed_cost": 0, "attempts": []}
    )
    prior_cost = ledger["observed_cost"]
    if prior_cost >= args.max_cost:
        raise ValueError("The shared observed-cost limit has already been reached")
    report = {
        "provider": "openrouter",
        "model": args.model,
        "upstream_requested": args.upstream,
        "real_model": True,
        "product_api": True,
        "observed_cost_limit": args.max_cost,
        "prior_observed_cost": prior_cost,
        "requests": [],
        "interventions": [],
        "checks": {},
    }
    started, models, failure = time.monotonic(), [], None
    response_usages = {}
    temporary = TemporaryDirectory(prefix="wavefunction-orchestration-")
    root = Path(temporary.name).resolve()
    report["temporary_root"] = str(root)
    services = None

    def measurements():  # Replaced once the services exist; the report needs it even if setup fails.
        return [], {}

    all_runs = list
    try:
        os.environ["WAVEFUNCTION_DATA_DIR"] = str(root / "data")
        os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
        from wavefunction import agent_factory, coding_workspace, github_cli, main
        from wavefunction.config import Settings
        from wavefunction.models import merge_usage, message_usage

        services = main.Services(Settings(data_dir=root / "data", development_auth_bypass=True))
        main.services = services
        services.secrets = MemorySecretStore()
        services.secrets.set("openrouter_api_key", key)
        services.models.secrets = services.secrets
        discover = coding_workspace.discover
        coding_workspace.discover = lambda *a, **kw: discover(*a, **kw, home=root)

        async def signed_out():
            return False

        github_cli.commands_signed_in = signed_out
        folder = root / "repository"
        folder.mkdir()
        for name, value in FIXTURES.items():
            (folder / name).write_text(value)
        git(folder, "init", "-b", "main")
        git(folder, "config", "user.name", "Synthetic verifier")
        git(folder, "config", "user.email", "verifier@example.invalid")
        git(folder, "config", "commit.gpgsign", "false")
        git(folder, "add", ".")
        git(folder, "commit", "-m", "Synthetic invoice fixture")
        build_middleware = agent_factory.build_middleware

        class FixtureGuard(AgentMiddleware):
            def __init__(self, context):
                self.folder = Path(context.working_directory).resolve()
                self.run_id = context.run_id

            async def awrap_model_call(self, request, handler):
                response = await handler(request)
                usage = {}
                for message in response.result:
                    usage = merge_usage(usage, message_usage(message))
                response_usages.setdefault(self.run_id, []).append(usage)
                return response

            async def awrap_tool_call(self, request, handler):
                call = request.tool_call

                def reject(message):
                    return ToolMessage(
                        content=message, tool_call_id=call["id"], name=call["name"], status="error"
                    )

                if call["name"] in {
                    "start_terminal",
                    "write_terminal",
                    "choose_repository_folder",
                    "request_folder_access",
                    "web_search",
                    "fetch_page",
                    "configure_github",
                }:
                    return reject(
                        "This synthetic fixture needs only file tools, helper tools and unit tests."
                    )
                if call["name"] == "run_command":
                    if not safe_command(call["args"]["command"]):
                        return reject(
                            "Fixture shell commands: python3 -m unittest -v, or read-only git status/diff/show/log/rev-parse. Use file tools for changes; test_report already checks the combined report."
                        )
                    inspect_python(self.folder)
                if call["name"] in {"write_file", "edit_file"}:
                    target = Path(call["args"]["path"])
                    target = (target if target.is_absolute() else self.folder / target).resolve()
                    if target.parent != self.folder or target.name not in SOURCE_NAMES:
                        return reject(
                            "Edit only the five feature source modules in your own checkout; preserve supplied fixtures."
                        )
                return await handler(request)

        agent_factory.build_middleware = lambda *a, **kw: [
            FixtureGuard(kw["context"]),
            *build_middleware(*a, **kw),
        ]

        def all_runs():
            with services.coding_workspace.db() as db:
                threads = [dict(row) for row in db.execute("SELECT id, conversation_id FROM coding_threads")]
            return [
                run
                for thread in threads
                for run in services.repository.list_conversation_runs(
                    services.coding_workspace.agent_id, thread["conversation_id"]
                )
            ]

        def measurements():
            calls, usage = [], {}
            for run in all_runs():
                observed = iter(response_usages.get(run["id"], []))
                for event in services.repository.list_run_events(run["id"]):
                    if event["event_type"] != "model_call_finished":
                        continue
                    counted = {}
                    for item in event["payload"].get("usage", []):
                        if item:
                            counted = merge_usage(counted, item)
                    metadata_usage = next(observed, {})
                    if "cost" in metadata_usage:
                        counted["cost"] = metadata_usage["cost"]
                    if counted:
                        usage = merge_usage(usage, counted)
                    calls.append(
                        {
                            "run_id": run["id"],
                            "call_id": event["payload"]["call_id"],
                            "usage": counted or None,
                            "cache_diagnostics": event["payload"].get("cache_diagnostics"),
                        }
                    )
            # Completed/failed runs also retain provider cost from response metadata. This
            # covers a final persisted result if a wrapper did not expose metadata itself.
            recorded_cost = sum(run.get("usage", {}).get("cost", 0) for run in all_runs())
            if recorded_cost > usage.get("cost", 0):
                usage["cost"] = recorded_cost
            return calls, usage

        build_model = services.models.chat_model

        def tracked_model(*a, **kw):
            model = build_model(*a, **kw)
            if args.upstream:
                model.extra_body = {
                    **(model.extra_body or {}),
                    "provider": {"only": [args.upstream], "allow_fallbacks": False},
                }
            models.append(model)

            async def request_hook(request):
                _, usage = measurements()
                if prior_cost + usage.get("cost", 0) >= args.max_cost:
                    raise RuntimeError("Shared observed-cost stop reached")
                if len(report["requests"]) >= args.max_requests:
                    raise RuntimeError("Live verification request limit reached")
                wire = json.loads(request.content)
                text = json.dumps(wire.get("messages", []))
                if not any(item.get("session_id") == wire.get("session_id") for item in report["requests"]):
                    report.setdefault("opening_prompts", []).append(
                        {
                            "session_id": wire.get("session_id"),
                            "system": [
                                message
                                for message in wire.get("messages", [])
                                if message.get("role") in {"system", "developer"}
                            ],
                            "tool_names": [tool["function"]["name"] for tool in wire.get("tools", [])],
                            "discovery": next(
                                (
                                    tool["function"]["description"]
                                    for tool in wire.get("tools", [])
                                    if tool["function"]["name"] == "search_available_tools"
                                ),
                                None,
                            ),
                        }
                    )
                report["requests"].append(
                    {
                        "number": len(report["requests"]) + 1,
                        "elapsed_seconds": round(time.monotonic() - started, 3),
                        "session_id": wire.get("session_id"),
                        "messages": len(wire.get("messages", [])),
                        "tools": len(wire.get("tools", [])),
                        "coordination_guidance_sent": "Coordinating helpers:" in text,
                        "helper_guidance_sent": "Working as a helper:" in text,
                    }
                )

            model.root_async_client._client.event_hooks = {"request": [request_hook]}
            return model

        services.models.chat_model = tracked_model
        parent_id = None
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app), base_url="http://test"
        ) as client:

            async def create(chat_folder):
                response = await client.post(
                    "/api/coding/threads",
                    json={
                        "folder": str(chat_folder),
                        "model": {
                            "provider": "openrouter",
                            "model": args.model,
                            "max_tokens": args.max_tokens,
                        },
                    },
                )
                response.raise_for_status()
                return response.json()

            async def send(thread, content):
                response = await client.post(
                    f"/api/coding/threads/{thread['id']}/messages", json={"content": content}
                )
                response.raise_for_status()
                return response.json()

            parent = await create(folder)
            parent_run = await send(parent, TASK)
            parent_id = parent_run["id"]
            report["parent_run_id"] = parent_id
            children, max_active, corrected = {}, 0, None
            correction_module = None
            addition_sent = False
            deadline = time.monotonic() + args.timeout
            last_progress = 0
            while time.monotonic() < deadline:
                current = services.repository.get_run(parent_id)
                events = services.repository.list_run_events(parent_id)
                for event in events:
                    if event["event_type"] == "delegation_started":
                        payload = event["payload"]
                        children[payload["run_id"]] = payload
                active = [
                    services.repository.get_run(identifier)
                    for identifier in children
                    if services.repository.get_run(identifier)["status"] == "running"
                ]
                max_active = max(max_active, len(active))
                # Interventions depend on real model choices, not timed fake tool replies.
                for helper in active:
                    assigned = re.search(
                        r"\b(billing|usage|exporting|report|returns)\.py\b", helper["input"]["text"].lower()
                    )
                    module = assigned[1] if assigned else None
                    if module in CORRECTIONS and not corrected:
                        response = await send(parent, CORRECTIONS[module])
                        corrected, correction_module = helper["id"], module
                        report["interventions"].append(
                            {
                                "kind": "correction",
                                "module": module,
                                "elapsed_seconds": round(time.monotonic() - started, 3),
                                "same_parent_run": response["id"] == parent_id,
                                "target_run_id": corrected,
                            }
                        )
                        break
                completed_calls = sum(event["event_type"] == "model_call_finished" for event in events)
                active_at_addition = [
                    identifier
                    for identifier in children
                    if services.repository.get_run(identifier)["status"] == "running"
                ]
                addition_trigger = (
                    "helpers_running" if len(active_at_addition) >= 2 else "parent_working_fallback"
                )
                may_add = len(active_at_addition) >= 2 or (
                    not children and completed_calls >= 2 and time.monotonic() - started >= 60
                )
                if current["status"] == "running" and may_add and not addition_sent:
                    response = await send(parent, ADDITION)
                    addition_sent = True
                    report["interventions"].append(
                        {
                            "kind": "addition",
                            "elapsed_seconds": round(time.monotonic() - started, 3),
                            "same_parent_run": response["id"] == parent_id,
                            "active_helpers": len(active_at_addition),
                            "trigger": addition_trigger,
                            "parent_completed_model_calls": completed_calls,
                        }
                    )
                _, usage = measurements()
                if time.monotonic() - last_progress >= 30:
                    last_progress = time.monotonic()
                    print(
                        json.dumps(
                            {
                                "progress": current["status"],
                                "elapsed_seconds": round(last_progress - started, 1),
                                "helpers": len(children),
                                "active_helpers": len(active),
                                "requests": len(report["requests"]),
                                "observed_cost": usage.get("cost"),
                                "interventions": len(report["interventions"]),
                            }
                        ),
                        flush=True,
                    )
                if prior_cost + usage.get("cost", 0) >= args.max_cost:
                    raise RuntimeError("Shared observed-cost stop reached")
                if (
                    current["status"] in {"completed", "failed", "cancelled"}
                    and parent_id not in services.runtime.tasks
                ):
                    report["helpers_at_parent_completion"] = [
                        {
                            "run_id": run["id"],
                            "status": run["status"],
                            "runtime_released": run["id"] not in services.runtime.tasks,
                            "error": run.get("error"),
                        }
                        for run in all_runs()
                        if run.get("parent_run_id") == parent_id
                    ]
                    break
                if current["status"].startswith("waiting_"):
                    raise RuntimeError(f"Parent unexpectedly paused: {current['status']}")
                await asyncio.sleep(0.2)
            else:
                raise TimeoutError("Real orchestration did not finish before its time limit")
            report["parent_status"] = current["status"]
            report["parent_output"] = current.get("output")
            report["max_active_helpers"] = max_active
            if current["status"] != "completed":
                report["checks"]["parent_completed"] = False
                raise RuntimeError(
                    f"Parent {current['status']}: {current.get('error') or 'no completed result'}. Behavioral checks were not run."
                )
            report["checks"].update(
                parent_completed=current["status"] == "completed",
                parallel_request_spawned_helpers=max_active >= 2,
                midrun_addition_delivered=addition_sent,
                correction_delivered_to_parent=bool(corrected),
                steering_reused_parent_run=bool(report["interventions"])
                and all(item["same_parent_run"] for item in report["interventions"]),
            )
            collected_results = set()
            for event in events:
                if event["event_type"] != "tool_completed" or event["payload"].get("tool") not in {
                    "get_agent",
                    "wait_agents",
                    "send_message",
                    "spawn_agent",
                }:
                    continue
                try:
                    result = json.loads(event["payload"]["result"])
                except (KeyError, ValueError, TypeError):
                    continue
                for item in result.get("agents", [result]):
                    if item.get("agent_id") and item.get("result_commit"):
                        collected_results.add((item["agent_id"], item["result_commit"]))
            child_results = []
            for identifier, payload in children.items():
                initial = services.repository.get_run(identifier)
                history = services.repository.list_conversation_runs(
                    services.coding_workspace.agent_id, initial["conversation_id"]
                )
                child = max(history, key=lambda run: run["started_at"])
                child_events = [
                    event for run in history for event in services.repository.list_run_events(run["id"])
                ]
                saved = await services.coding_workspace.checkpoints.child_diff(
                    payload["thread_id"], child["id"]
                )
                marker = {"billing": "7500", "usage": "exclusive", "exporting": "semicolon"}.get(
                    correction_module, ""
                )
                corrected_ids = {
                    event["id"]
                    for event in child_events
                    if event["event_type"] == "steering_requested" and marker in str(event["payload"]).lower()
                }
                correction_mode = None
                if identifier == corrected:
                    if any(
                        event["event_type"] == "steering_applied"
                        and event["payload"]["event_id"] in corrected_ids
                        for event in child_events
                    ):
                        correction_mode = "steering"
                    elif any(
                        run["id"] != identifier and marker in run["input"]["text"].lower() for run in history
                    ):
                        correction_mode = "followup_after_completion"
                matches = []
                for name in saved.get("files", []):
                    if name not in SOURCE_NAMES or not (folder / name).is_file():
                        continue
                    tip = saved.get("result_commit")
                    if tip:
                        contents = git(folder, "show", f"{tip}:{name}")
                        if contents == (folder / name).read_text().strip():
                            matches.append(name)
                child_results.append(
                    {
                        "run_id": child["id"],
                        "initial_run_id": identifier,
                        "thread_id": payload["thread_id"],
                        "title": payload["agent_name"],
                        "status": child["status"],
                        "run_durations_ms": [run["duration_ms"] for run in history],
                        "files": saved.get("files"),
                        "base_commit": saved.get("base_commit"),
                        "result_commit": saved.get("result_commit"),
                        "result_ref": saved.get("result_ref"),
                        "parent_collected_saved_result": (payload["thread_id"], saved.get("result_commit"))
                        in collected_results,
                        "parent_matching_saved_files": matches,
                        "correction_applied": bool(correction_mode),
                        "correction_mode": correction_mode,
                    }
                )
            report["helpers"] = child_results
            completion_states = report["helpers_at_parent_completion"]
            report["helper_failures"] = [item for item in completion_states if item["status"] == "failed"]
            report["checks"].update(
                helper_correction_applied=any(item["correction_applied"] for item in child_results),
                useful_saved_results_integrated=sum(
                    bool(item["parent_matching_saved_files"]) and item["parent_collected_saved_result"]
                    for item in child_results
                )
                >= 2,
                helpers_settled=bool(completion_states)
                and all(
                    item["status"] in {"completed", "failed", "cancelled"} and item["runtime_released"]
                    for item in completion_states
                ),
                immutable_fixtures_preserved=all(
                    (folder / name).read_text() == content
                    for name, content in FIXTURES.items()
                    if name not in SOURCE_NAMES
                ),
            )
            inspect_python(folder)
            verified = await asyncio.to_thread(
                subprocess.run,
                ["python3", "-B", "-I", "-c", FINAL_CHECKS, str(folder), correction_module or "none"],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            report["independent_validation"] = {
                "exit_code": verified.returncode,
                "stdout": verified.stdout,
                "stderr": verified.stderr,
            }
            report["checks"]["correct_parent_checkout"] = verified.returncode == 0
            report["source_sha256"] = {
                path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in folder.glob("*.py")
                if path.name in SOURCE_NAMES
            }
            # A separate simple request must not inherit this conversation's helper need.
            control = root / "simple"
            control.mkdir()
            (control / "settings.json").write_text('{"queue":"invoices","retries":3}\n')
            control_thread = await create(control)
            run = await send(
                control_thread,
                "Read settings.json and tell me the queue name and retry count. No changes are needed.",
            )
            final = await services.runtime.wait(run["id"], timeout=180)
            control_events = services.repository.list_run_events(run["id"])
            report["control"] = {
                "run_id": run["id"],
                "status": final["status"],
                "output": final.get("output"),
            }
            report["control"]["wall_seconds"] = (final.get("duration_ms") or 0) / 1000
            report["checks"]["simple_task_did_not_delegate"] = final["status"] == "completed" and not any(
                event["event_type"] == "delegation_started" for event in control_events
            )
            report["checks"]["simple_task_correct"] = all(
                word in str(final.get("output")) for word in ("invoices", "3")
            )
            failed = [name for name, passed in report["checks"].items() if not passed]
            if failed:
                raise AssertionError("Live checks failed: " + ", ".join(failed))
    except Exception as error:  # noqa: BLE001 — persist evidence and clean exact owned fixtures
        failure = error
        report["error"] = str(error).replace(key, "[REDACTED]")
    finally:
        if services is not None:
            for run_id, task in list(services.runtime.tasks.items()):
                if not task.done():
                    await services.runtime.cancel(run_id)
        report["calls"], report["usage"] = measurements()
        report["runs"] = [
            {
                "run_id": run["id"],
                "kind": run["kind"],
                "status": run["status"],
                "wall_seconds": (run.get("duration_ms") or 0) / 1000,
                "usage": run.get("usage"),
            }
            for run in all_runs()
        ]
        report["usage_coverage"] = {
            "calls": len(report["calls"]),
            "calls_with_usage": sum(bool(item["usage"]) for item in report["calls"]),
            "calls_with_reported_cost": sum("cost" in (item["usage"] or {}) for item in report["calls"]),
            "calls_with_reported_cache": sum(
                "cache_read" in (item["usage"] or {}).get("input_token_details", {})
                for item in report["calls"]
            ),
        }
        report["trace"] = [
            {
                "run_id": run["id"],
                "kind": run["kind"],
                "status": run["status"],
                "error": run.get("error"),
                "input": run["input"]["text"],
                "events": [
                    event
                    for event in services.repository.list_run_events(run["id"])
                    if event["event_type"]
                    in {
                        "delegation_started",
                        "tool_started",
                        "tool_completed",
                        "tool_failed",
                        "steering_requested",
                        "steering_applied",
                        "model_call_failed",
                        "run_questions",
                    }
                ],
            }
            for run in all_runs()
        ]
        if services is not None:
            await services.runtime.aclose()
            await services.computer.shutdown()
            await services.coding_workspace.terminals.shutdown()
            await services.github_proxy.close()
        for model in models:
            model.root_client.close()
            await model.root_async_client.close()
        if services is not None and hasattr(services.secrets, "values"):
            services.secrets.values.clear()
        report["wall_seconds"] = round(time.monotonic() - started, 3)
        report["remaining_runtime_tasks"] = len(services.runtime.tasks) if services is not None else 0
        temporary.cleanup()  # Only this verifier's entire synthetic data/repository/worktree root.
        report["cleanup"] = {"runtime_closed": True, "temporary_workspace_removed": not root.exists()}
        observed = report["usage"].get("cost", 0)
        ledger["observed_cost"] = round(prior_cost + observed, 10)
        ledger["attempts"].append(
            {"report": str(args.report), "observed_cost": observed, "error": report.get("error")}
        )
        args.cost_ledger.parent.mkdir(parents=True, exist_ok=True)
        args.cost_ledger.write_text(json.dumps(ledger, indent=2) + "\n")
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2).replace(key, "[REDACTED]") + "\n")
        print(
            json.dumps(
                {
                    key: value
                    for key, value in report.items()
                    if key not in {"trace", "calls", "requests", "parent_output", "opening_prompts"}
                }
            ),
            flush=True,
        )
    if failure:
        raise RuntimeError(report["error"]) from None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key-file", type=Path, help="Read-only env file containing OpenRouter key")
    parser.add_argument("--model", default="deepseek/deepseek-v4-pro-0813")
    parser.add_argument(
        "--upstream", help="Optional eligible provider slug; omitted uses normal account-eligible routing"
    )
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument(
        "--cost-ledger", type=Path, required=True, help="Shared observed-cost accounting across retries"
    )
    parser.add_argument("--max-cost", type=float, default=2)
    parser.add_argument("--max-requests", type=int, default=100)
    parser.add_argument("--max-tokens", type=int, default=16384)
    parser.add_argument("--timeout", type=float, default=900)
    asyncio.run(verify(parser.parse_args()))
