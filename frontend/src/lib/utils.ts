/** Joins class names, dropping anything falsy. Same helper as the Borzo site. */
export function cn(...classes: Array<string | false | null | undefined>) {
  return classes.filter(Boolean).join(" ");
}
