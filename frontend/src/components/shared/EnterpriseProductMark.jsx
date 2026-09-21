import logoSvg from "../../assets/retailgpt-logo.svg";

const SIZE_PRESETS = {
  sm: { box: "h-9 w-9", img: "h-7 w-7" },
  md: { box: "h-11 w-11", img: "h-9 w-9" },
  lg: { box: "h-14 w-14", img: "h-11 w-11" },
};

/**
 * Brand mark on a neutral layout box (no frame).
 *
 * `tone` picks how the mark is painted: "mono" flattens it to near-black (the original
 * behaviour, for dense monochrome contexts), "brand" leaves the logo its own colours and
 * softens it so it sits quietly in a card footer rather than competing with the title.
 *
 * @param {{ size?: 'sm' | 'md' | 'lg', shape?: 'rounded' | 'circle', tone?: 'mono' | 'brand', className?: string }} props
 */
export function EnterpriseProductMark({ size = "md", shape = "rounded", tone = "mono", className = "" }) {
  const preset = SIZE_PRESETS[size] ?? SIZE_PRESETS.md;
  const radius = shape === "circle" ? "rounded-full" : "rounded-lg";
  const paint = tone === "brand" ? "opacity-75" : "brightness-0 dark:invert";
  return (
    <div
      className={`flex ${preset.box} shrink-0 items-center justify-center ${radius} ${className}`}
      aria-hidden
    >
      <img src={logoSvg} alt="" className={`${preset.img} object-contain ${paint}`} />
    </div>
  );
}
