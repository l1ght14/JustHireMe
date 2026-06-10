import type { Cfg } from "./shared";
import { BigToggle, SectionLabel } from "./shared";

const INTERVAL_OPTIONS = [
  { value: "6",  label: "Every 6 hours" },
  { value: "12", label: "Every 12 hours" },
  { value: "24", label: "Once a day" },
  { value: "48", label: "Every 2 days" },
];

export function AutomationSettings({ cfg, onChange }: { cfg: Cfg; onChange: (k: keyof Cfg, v: string) => void }) {
  const intervalHours = cfg.scan_interval_hours || "24";

  return (
    <div style={{ borderTop: "1px dashed var(--line)", paddingTop: 18 }}>
      <SectionLabel label="Auto Scan" sub="runs in the background" />

      {/* Auto-scan toggle — Ghost Mode renamed to something clear */}
      <div style={{ display: "flex", flexDirection: "column", gap: 8, marginBottom: 14 }}>
        <BigToggle
          active={cfg.ghost_mode === "true"}
          onToggle={() => onChange("ghost_mode", cfg.ghost_mode === "true" ? "false" : "true")}
          icon="refresh"
          tone="purple"
          label="Auto Scan & Rank"
          badge={cfg.ghost_mode === "true" ? "on" : "off"}
          sub={
            cfg.ghost_mode === "true"
              ? `Scanning automatically every ${intervalHours}h — new jobs appear in your Pipeline`
              : "Turn on to scan for new jobs automatically on a schedule"
          }
        />

        {/* Interval picker — only shown when auto scan is on */}
        {cfg.ghost_mode === "true" && (
          <div style={{ paddingLeft: 12, display: "flex", alignItems: "center", gap: 10 }}>
            <span style={{ fontSize: 12, color: "var(--ink-3)", minWidth: 90 }}>Scan every</span>
            <select
              value={intervalHours}
              onChange={e => onChange("scan_interval_hours", e.target.value)}
              style={{
                fontSize: 12,
                padding: "4px 8px",
                borderRadius: 6,
                border: "1px solid var(--line)",
                background: "var(--surface)",
                color: "var(--ink)",
                cursor: "pointer",
              }}
            >
              {INTERVAL_OPTIONS.map(opt => (
                <option key={opt.value} value={opt.value}>{opt.label}</option>
              ))}
            </select>
            <span style={{ fontSize: 11, color: "var(--ink-3)" }}>
              New jobs are ranked against your profile automatically
            </span>
          </div>
        )}
      </div>

      <SectionLabel label="Browser Automation" sub="form fill assistant" />
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <BigToggle
          active={cfg.headed_browser === "true"}
          onToggle={() => onChange("headed_browser", cfg.headed_browser === "true" ? "false" : "true")}
          icon="globe"
          tone="blue"
          label="Headed Browser"
          badge={cfg.headed_browser === "true" ? "visible" : "headless"}
          sub="Show the browser window when filling application forms — lets you review and submit yourself"
        />

        <BigToggle
          active={cfg.auto_apply === "true"}
          onToggle={() => onChange("auto_apply", cfg.auto_apply === "true" ? "false" : "true")}
          icon="fire"
          tone="orange"
          label="Auto Submit"
          badge={cfg.auto_apply === "true" ? "on — submits automatically" : "off"}
          sub={
            cfg.auto_apply === "true"
              ? "WARNING: app will click Submit on your behalf — review form carefully first"
              : "Off — app fills the form but you click Submit. Recommended."
          }
        />
      </div>
    </div>
  );
}
