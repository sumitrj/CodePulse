export function cartTotal(prices: number[]): number {
  return prices.reduce((sum, p) => sum + p, 0);
}

document.getElementById("pay")?.addEventListener("click", () => cartTotal([10, 10]));
