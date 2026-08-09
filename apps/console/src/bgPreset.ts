/** Landing hero photos via `?bg=` — files in `public/images/`. */
export const BG_PRESETS = [
  "sky",
  "mist",
  "dusk",
  "field",
  "dawn",
  "forest",
  "bloom",
  "meadow",
  "cloud",
] as const;
export type BgPreset = (typeof BG_PRESETS)[number];

export const BG_IMAGES: Record<BgPreset, string> = {
  sky: "/images/hero-sky.jpg",
  mist: "/images/hero-mist.jpg",
  dusk: "/images/hero-dusk.jpg",
  field: "/images/hero-field.jpg",
  dawn: "/images/hero-dawn.jpg",
  forest: "/images/hero-forest.jpg",
  bloom: "/images/hero-bloom.jpg",
  meadow: "/images/hero-meadow.jpg",
  cloud: "/images/hero-cloud.jpg",
};

export function bgPresetFromSearch(search = window.location.search): BgPreset {
  try {
    const v = new URLSearchParams(search).get("bg");
    if (v && (BG_PRESETS as readonly string[]).includes(v)) return v as BgPreset;
  } catch {
    /* ignore */
  }
  return "sky";
}
