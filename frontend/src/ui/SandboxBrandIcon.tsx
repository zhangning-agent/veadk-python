import hermesLogo from "../assets/hermes.svg";
import openClawLogo from "../assets/openclaw.svg";
import "./SandboxBrandIcon.css";

export type SandboxBrand = "openclaw" | "hermes";

export function SandboxBrandIcon({
  brand,
  className = "",
}: {
  brand: SandboxBrand;
  className?: string;
}) {
  const label = brand === "openclaw" ? "OpenClaw" : "Hermes";
  return (
    <img
      className={`sandbox-brand-icon sandbox-brand-icon--${brand}${className ? ` ${className}` : ""}`}
      src={brand === "openclaw" ? openClawLogo : hermesLogo}
      alt=""
      aria-hidden="true"
      title={`${label} logo`}
    />
  );
}
