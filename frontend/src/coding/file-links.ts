import { createContext } from "react";

/** Opens a file the chat links (by its path) in the chat's preview. */
export const FileLinks = createContext<((path: string) => void) | null>(null);
/** The path a link names, or null for web links, anchors and other schemes. */
export function linkedFile(href: string | undefined) {
  if (!href || href.startsWith("#") || /^[a-z][a-z0-9+.-]*:/i.test(href)) return null;
  // A line anchor or query (notes.md#L10) is not part of the file's name.
  const path = href.replace(/[?#].*$/, "");
  try {
    return decodeURIComponent(path);
  } catch {
    return path;
  }
}

export const MAX_ROWS = 500;

/** Split CSV/TSV text into rows, honouring quoted fields. */
export function parseTable(text: string, separator: string) {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let quoted = false;
  // Spreadsheet exports often start with a byte order mark. The header plus one row past the
  // limit is enough to know the table was cut.
  if (text.startsWith("\ufeff")) text = text.slice(1);
  for (let index = 0; index < text.length && rows.length < MAX_ROWS + 2; index++) {
    const char = text[index];
    if (quoted) {
      if (char === '"' && text[index + 1] === '"') {
        field += '"';
        index++;
      } else if (char === '"') quoted = false;
      else field += char;
    } else if (char === '"' && !field) quoted = true;
    else if (char === separator) {
      row.push(field);
      field = "";
    } else if (char === "\n" || char === "\r") {
      if (char === "\r" && text[index + 1] === "\n") index++;
      row.push(field);
      rows.push(row);
      row = [];
      field = "";
    } else field += char;
  }
  if (field || row.length) rows.push([...row, field]);
  return rows;
}
