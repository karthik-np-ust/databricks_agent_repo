import { createDarkTheme, createLightTheme, type BrandVariants, type Theme } from "@fluentui/react-components";

// UST brand: Dark Teal #006E74 drives the Fluent brand ramp (buttons, links, selection).
// Shades 10-70 darken toward Petrol-black, 80 is the brand color, 90-160 lighten toward white.
const BRAND = "#006E74";

function mix(hex: string, target: string, amount: number): string {
  const a = hex.match(/\w\w/g)!.map((h) => parseInt(h, 16));
  const b = target.match(/\w\w/g)!.map((h) => parseInt(h, 16));
  return "#" + a.map((v, i) => Math.round(v + (b[i] - v) * amount).toString(16).padStart(2, "0")).join("");
}

const steps = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120, 130, 140, 150, 160] as const;
const brand = Object.fromEntries(
  steps.map((s) => [s, s < 80 ? mix(BRAND, "#000000", (80 - s) / 80) : mix(BRAND, "#ffffff", ((s - 80) / 80) * 0.92)]),
) as unknown as BrandVariants;

const FONT = '"Aptos", "Segoe UI Variable", "Segoe UI", system-ui, -apple-system, sans-serif';

export const lightTheme: Theme = {
  ...createLightTheme(brand),
  fontFamilyBase: FONT,
  colorNeutralBackground2: "#F2F7F8", // UST Light Gray Wash (page plane)
  colorNeutralForeground1: "#231F20", // UST Soft Black
};

export const darkTheme: Theme = {
  ...createDarkTheme(brand),
  fontFamilyBase: FONT,
  colorBrandForeground1: brand[110],
  colorBrandForeground2: brand[120],
};
