"""Exercise the real isolated computer and clean up only this verification's resources."""

import argparse
import asyncio
import io
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

parser = argparse.ArgumentParser()
parser.add_argument("--evidence", type=Path)
parser.add_argument(
    "--build", action="store_true", help="Build a missing image for this check and remove it afterwards."
)
args = parser.parse_args()


async def main():
    with TemporaryDirectory(prefix="harness-computer-") as temporary:
        root = Path(temporary)
        os.environ["WAVEFUNCTION_DATA_DIR"] = str(root / "data")
        from wavefunction.computer import DesktopManager

        manager = DesktopManager(data_dir=root / "data")
        before_image = await manager.refresh_status()
        conversation, copy = "harness-synthetic-computer", "harness-synthetic-thread"
        volume = manager.home_volume(copy)
        container_id = None
        result = {}
        built = False
        try:
            if not before_image.image_ready:
                if not args.build:
                    raise RuntimeError(
                        "Build the computer image in Settings first, or use --build. This check never installs or restarts Docker."
                    )
                built = True
                manager.start_build()
                await manager._build_task
                assert manager._status.image_ready, manager._status.build_log[-10:]
            session = await manager.session(conversation, copy)
            container_id = session.container_id
            response = await session.client.get("/screenshot")
            response.raise_for_status()
            assert response.content.startswith(b"\x89PNG"), "The computer must return an actual screenshot."
            from PIL import Image

            assert Image.open(io.BytesIO(response.content)).convert("RGB").getbbox(), (
                "A ready desktop cannot be black."
            )
            response = await session.client.post("/key", json={"keys": "ctrl+l"})
            response.raise_for_status()
            await asyncio.sleep(0.7)
            for operation, payload in [
                (
                    "type",
                    {
                        "text": "data:text/html,<title>HARNESS-COMPUTER-PASS</title><body style='background:%2318181b;color:white;font:32px monospace;padding:80px'>Harness computer use verified</body>"
                    },
                ),
                ("key", {"keys": "Return"}),
            ]:
                response = await session.client.post("/" + operation, json=payload)
                response.raise_for_status()
            await asyncio.sleep(1)
            title = await manager._run(
                manager.docker(), "exec", container_id, "xdotool", "getactivewindow", "getwindowname"
            )
            assert "HARNESS-COMPUTER-PASS" in title.stdout, (
                "The browser must actually open the page typed through computer tools."
            )
            response = await session.client.get("/screenshot")
            response.raise_for_status()
            if args.evidence:
                args.evidence.parent.mkdir(parents=True, exist_ok=True)
                args.evidence.write_bytes(response.content)
            result = {
                "real_screenshot": True,
                "keyboard": True,
                "typing": True,
                "browser_page_verified": True,
                "container": container_id,
            }
        finally:
            await manager.forget([copy])
            await manager.shutdown()
            docker = manager.docker()
            container = (
                await manager._run(docker, "ps", "-aq", "--filter", "id=" + container_id)
                if container_id
                else None
            )
            volumes = await manager._run(docker, "volume", "ls", "-q", "--filter", "name=" + volume)
            result["cleanup"] = {
                "container_removed": container is None or not container.stdout.strip(),
                "home_removed": volume not in volumes.stdout.split(),
            }
            if built:
                await manager._run(docker, "image", "rm", before_image.image_tag, timeout=30)
                remains = await manager._run(docker, "image", "ls", "-q", before_image.image_tag)
                result["cleanup"]["test_image_removed"] = not remains.stdout.strip()
            print(json.dumps(result), flush=True)
            assert all(result["cleanup"].values()), "Synthetic computer resources must be removed."


asyncio.run(main())
