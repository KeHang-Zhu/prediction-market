// Tiny shared layout atoms for the control bars — a thin vertical rule that
// separates control clusters so the eye parses groups and wrapping happens at
// group boundaries, not mid-cluster.
export function Divider() {
  return <span aria-hidden className="hidden h-5 w-px shrink-0 bg-slate-200 sm:inline-block" />
}
