import { useEffect, useState, type ReactNode } from "react";
import { BarChart3, LoaderCircle, RefreshCw } from "lucide-react";
import { NativeSection, NativeView, ViewHeading } from "@/components/native-view";
import { Button } from "@/components/ui/button";
import { codingApi, type AnalyticsMetric, type AnalyticsStats, type CodingAnalytics } from "./api";
import "./analytics.css";

const count = (value: number) => value.toLocaleString();
// Summary figures only grow, so they shorten to K, M, B and T; the exact value is in the tooltip.
const compactCount = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });
const compact = (value: number) => compactCount.format(value);
const compactDollars = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  notation: "compact",
  maximumFractionDigits: 2,
});
const dollars = new Intl.NumberFormat(undefined, {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 6,
});
const dayFormat = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", timeZone: "UTC" });
const fullDayFormat = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeZone: "UTC" });

function duration(value: number) {
  if (value > 0 && value < 1000) return "<1s";
  const seconds = Math.floor(value / 1000);
  const minutes = Math.floor(seconds / 60);
  const hours = Math.floor(minutes / 60);
  if (hours) return `${count(hours)}h ${minutes % 60}m`;
  if (minutes) return `${minutes}m ${seconds % 60}s`;
  return `${seconds}s`;
}

function coverage(metric: AnalyticsMetric, runs: number) {
  if (metric.value === null) return "Not reported";
  return metric.known_runs < runs ? `Reported for ${count(metric.known_runs)} of ${count(runs)} runs` : "";
}

function MetricValue({
  metric,
  runs,
  format = count,
  exact,
  showCoverage = true,
}: {
  metric: AnalyticsMetric;
  runs: number;
  format?: (value: number) => string;
  exact?: (value: number) => string;
  showCoverage?: boolean;
}) {
  const detail = showCoverage ? coverage(metric, runs) : "";
  return (
    <span className="coding-analytics-metric">
      <span title={metric.value !== null && exact ? exact(metric.value) : undefined}>
        {metric.value === null ? "Unknown" : format(metric.value)}
      </span>
      {detail ? <small>{detail}</small> : null}
    </span>
  );
}

function SummaryCard({
  title,
  children,
  detail,
}: {
  title: string;
  children: ReactNode;
  detail?: ReactNode;
}) {
  return (
    <div className="coding-analytics-card">
      <dt>{title}</dt>
      <dd>
        {children}
        {detail ? <p>{detail}</p> : null}
      </dd>
    </div>
  );
}

function Summary({ stats }: { stats: AnalyticsStats }) {
  return (
    <dl className="coding-analytics-summary" aria-label="All-time usage summary">
      <SummaryCard title="Input tokens">
        <MetricValue metric={stats.input_tokens} runs={stats.runs} format={compact} exact={count} />
      </SummaryCard>
      <SummaryCard title="Cached input" detail="Included in input tokens">
        <MetricValue metric={stats.cached_input_tokens} runs={stats.runs} format={compact} exact={count} />
      </SummaryCard>
      <SummaryCard title="Uncached input">
        <MetricValue metric={stats.uncached_input_tokens} runs={stats.runs} format={compact} exact={count} />
      </SummaryCard>
      <SummaryCard title="Output tokens">
        <MetricValue metric={stats.output_tokens} runs={stats.runs} format={compact} exact={count} />
      </SummaryCard>
      <SummaryCard title="Runs" detail={`${count(stats.user_requests)} user requests`}>
        <span title={count(stats.runs)}>{compact(stats.runs)}</span>
      </SummaryCard>
      <SummaryCard
        title="Failed runs"
        detail={`${count(stats.completed_runs)} completed · ${count(stats.cancelled_runs)} cancelled · ${count(stats.active_runs)} active`}
      >
        {count(stats.failed_runs)}
      </SummaryCard>
      <SummaryCard
        title="Recorded working time"
        detail={
          stats.average_duration_ms.value === null
            ? undefined
            : `${duration(stats.average_duration_ms.value)} average per timed run`
        }
      >
        <MetricValue metric={stats.duration_ms} runs={stats.runs} format={duration} />
      </SummaryCard>
      <SummaryCard title="Reported cost (USD)">
        <MetricValue
          metric={stats.cost_usd}
          runs={stats.runs}
          format={(value) => (value > 0 && value < 0.005 ? "<$0.01" : compactDollars.format(value))}
          exact={(value) => dollars.format(value)}
        />
      </SummaryCard>
    </dl>
  );
}

function DailyActivity({ daily }: { daily: CodingAnalytics["daily"] }) {
  const peak = Math.max(1, ...daily.map((day) => day.total_tokens.value ?? 0));
  return (
    <NativeSection
      title="Daily activity"
      description="Last 14 days · recorded tokens by run start date (UTC)."
    >
      <div className="coding-analytics-chart-card">
        <ol className="coding-analytics-chart" aria-label="Daily token usage">
          {daily.map((day) => {
            const date = new Date(`${day.date}T00:00:00Z`);
            const value = day.total_tokens.value;
            const partial = coverage(day.total_tokens, day.runs);
            const label = `${fullDayFormat.format(date)}: ${value === null ? "token usage unknown" : `${count(value)} recorded tokens`}, ${count(day.runs)} runs${partial ? `. ${partial}` : ""}`;
            return (
              <li key={day.date}>
                <div className="coding-analytics-bar" role="img" aria-label={label} title={label}>
                  <span className="coding-analytics-bar-count" aria-hidden="true">
                    {value === null ? "?" : compactCount.format(value)}
                  </span>
                  <div className="coding-analytics-bar-track" aria-hidden="true">
                    <span style={{ height: `${((value ?? 0) / peak) * 100}%` }} />
                  </div>
                </div>
                <span className="coding-analytics-date" aria-hidden="true">
                  {dayFormat.format(date)}
                </span>
              </li>
            );
          })}
        </ol>
        <details className="coding-analytics-daily-details">
          <summary>Daily breakdown</summary>
          <div
            className="coding-analytics-table-scroll"
            tabIndex={0}
            role="region"
            aria-label="Daily usage details"
          >
            <table>
              <caption className="sr-only">Recorded daily usage in UTC</caption>
              <thead>
                <tr>
                  <th scope="col">Date</th>
                  <th scope="col">Runs</th>
                  <th scope="col">Input</th>
                  <th scope="col">Cached input</th>
                  <th scope="col">Uncached input</th>
                  <th scope="col">Output</th>
                  <th scope="col">Reported cost (USD)</th>
                </tr>
              </thead>
              <tbody>
                {daily.map((day) => (
                  <tr key={day.date}>
                    <th scope="row">{fullDayFormat.format(new Date(`${day.date}T00:00:00Z`))}</th>
                    <td>{count(day.runs)}</td>
                    <td>
                      <MetricValue metric={day.input_tokens} runs={day.runs} />
                    </td>
                    <td>
                      <MetricValue metric={day.cached_input_tokens} runs={day.runs} />
                    </td>
                    <td>
                      <MetricValue metric={day.uncached_input_tokens} runs={day.runs} />
                    </td>
                    <td>
                      <MetricValue metric={day.output_tokens} runs={day.runs} />
                    </td>
                    <td>
                      <MetricValue
                        metric={day.cost_usd}
                        runs={day.runs}
                        format={(value) => dollars.format(value)}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      </div>
    </NativeSection>
  );
}

function ModelUsage({ models }: { models: CodingAnalytics["models"] }) {
  return (
    <NativeSection
      title="Usage by model"
      description="All recorded runs, grouped by their captured provider and model."
    >
      <div
        className="coding-analytics-table-scroll coding-analytics-models"
        tabIndex={0}
        role="region"
        aria-label="Model usage"
      >
        <table>
          <caption className="sr-only">All-time recorded usage by model</caption>
          <thead>
            <tr>
              <th scope="col">Model</th>
              <th scope="col">Runs</th>
              <th scope="col">Input</th>
              <th scope="col">Cached input</th>
              <th scope="col">Uncached input</th>
              <th scope="col">Output</th>
              <th scope="col">Working time</th>
              <th scope="col">Reported cost (USD)</th>
            </tr>
          </thead>
          <tbody>
            {models.map((model) => (
              <tr key={JSON.stringify([model.provider, model.model])}>
                <th scope="row">{model.model || "Unknown model"}</th>
                <td>{count(model.runs)}</td>
                <td>
                  <MetricValue metric={model.input_tokens} runs={model.runs} showCoverage={false} />
                </td>
                <td>
                  <MetricValue metric={model.cached_input_tokens} runs={model.runs} showCoverage={false} />
                </td>
                <td>
                  <MetricValue metric={model.uncached_input_tokens} runs={model.runs} showCoverage={false} />
                </td>
                <td>
                  <MetricValue metric={model.output_tokens} runs={model.runs} showCoverage={false} />
                </td>
                <td>
                  <MetricValue
                    metric={model.duration_ms}
                    runs={model.runs}
                    format={duration}
                    showCoverage={false}
                  />
                </td>
                <td>
                  <MetricValue
                    metric={model.cost_usd}
                    runs={model.runs}
                    format={(value) => dollars.format(value)}
                    showCoverage={false}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </NativeSection>
  );
}

export function AnalyticsSettings() {
  const [data, setData] = useState<CodingAnalytics | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    void codingApi
      .analytics(controller.signal)
      .then((result) => {
        if (!controller.signal.aborted) setData(result);
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : String(reason));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [attempt]);
  function refresh() {
    setError(null);
    setLoading(true);
    setAttempt((value) => value + 1);
  }
  return (
    <NativeView>
      <ViewHeading
        title="Analytics"
        actions={
          <>
            {data ? (
              <p className="coding-analytics-updated">
                Last updated{" "}
                <time dateTime={data.generated_at}>{new Date(data.generated_at).toLocaleString()}</time>
              </p>
            ) : null}
            <Button
              size="sm"
              variant="secondary"
              onClick={refresh}
              disabled={loading}
              aria-label="Refresh analytics"
            >
              {loading ? <LoaderCircle className="animate-spin" /> : <RefreshCw />}
              Refresh
            </Button>
          </>
        }
      />
      {error ? (
        <div className="coding-analytics-error" role="alert">
          <div>
            <strong>Couldn’t load analytics</strong>
            <p>{error}</p>
          </div>
          <Button size="sm" variant="secondary" onClick={refresh}>
            Retry
          </Button>
        </div>
      ) : null}
      {loading ? (
        <p className="coding-analytics-loading" role="status">
          {data ? "Refreshing analytics…" : "Loading analytics…"}
        </p>
      ) : null}
      {data ? (
        data.totals.runs === 0 ? (
          <div className="coding-analytics-empty">
            <BarChart3 aria-hidden="true" />
            <h3>No recorded activity yet</h3>
            <p>Usage from your chats will appear here after you start working with a model.</p>
          </div>
        ) : (
          <>
            <Summary stats={data.totals} />
            <DailyActivity daily={data.daily} />
            <ModelUsage models={data.models} />
          </>
        )
      ) : null}
    </NativeView>
  );
}
