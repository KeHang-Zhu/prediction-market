export const money = (cents: number): string =>
  (cents / 100).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })

export const signedMoney = (cents: number): string =>
  (cents >= 0 ? '+' : '') + money(cents)

export const cent = (c: number | null | undefined): string =>
  c == null ? '–' : `${c}¢`

export const pct = (c: number | null | undefined): string =>
  c == null ? '–' : `${c}%`
