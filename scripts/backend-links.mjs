// Tauri copies the backend's resources into the app following symbolic links, so each of the
// sidecar's links (aliases of its libraries, about 9 MB in all) becomes a second copy of a
// file. Put the links back before the app is signed.
import { existsSync, lstatSync, readdirSync, readlinkSync, statSync, symlinkSync, unlinkSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";

export function restoreBackendLinks(root, app) {
  const source = join(root, "backend/dist/wavefunction-backend");
  const bundled = join(app, "Contents/Resources/backend");
  if (!existsSync(source))
    throw new Error(`The backend this app was built from is missing: ${source}. Run npm run sidecar:build.`);
  let restored = 0;
  const walk = (folder) => {
    for (const entry of readdirSync(folder)) {
      const path = join(folder, entry);
      const stat = lstatSync(path);
      if (stat.isDirectory()) walk(path);
      if (!stat.isSymbolicLink()) continue;
      const copy = join(bundled, relative(source, path));
      const target = readlinkSync(path);
      const linked = resolve(dirname(copy), target);
      const copied = lstatSync(copy, { throwIfNoEntry: false });
      if (copied?.isSymbolicLink()) continue;
      // Only a plain copy of a file the bundle itself holds becomes a link again.
      if (!copied?.isFile() || relative(bundled, linked).startsWith("..")
          || !statSync(linked, { throwIfNoEntry: false })?.isFile()) {
        console.warn(`Left the bundled copy of ${relative(source, path)} as it is.`);
        continue;
      }
      unlinkSync(copy);
      symlinkSync(target, copy);
      restored += 1;
    }
  };
  walk(source);
  console.log(`Restored ${restored} backend symbolic links in ${app}`);
  return restored;
}
