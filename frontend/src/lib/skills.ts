/** A readable title for a skill. Tool calls and slash commands keep using its saved name. */
export function skillDisplayName(name: string): string {
  const words = name.replaceAll("-", " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}
