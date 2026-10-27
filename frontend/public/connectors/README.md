# Connector logos

These assets are bundled locally. The connector UI does not use a third-party logo CDN.
`manifest.json` records each asset's source, whether it is a single-color mark, and its
published brand color when the SVG needs a fill color.

Single-color marks come from [Simple Icons 16.30.0](https://github.com/simple-icons/simple-icons/tree/16.30.0)
(CC0; license included), with the exact colors from its [brand metadata](https://github.com/simple-icons/simple-icons/blob/16.30.0/data/simple-icons.json).
Multicolor logos come from the respective services' official websites, repositories, and published favicon assets. Brand marks
remain trademarks of their respective owners and are used to identify integrations.

Email is provider-neutral and uses the existing Lucide Mail icon; Google uses
its full-color mark. Gmail and Outlook use the original SVGs from their official product
pages, with source URLs recorded in the manifest. Unknown custom MCP servers use a generic connector icon.
Preserve original brand colors, including black and white for monochrome brands such as
Vercel and Notion. Multicolor assets (including Figma, Airtable, ClickUp, and Slack) are
rendered unchanged, without masking, tinting, or inversion. A neutral white tile keeps the
marks readable in either app theme; it does not add an invented brand color.
