import { useEffect, useState } from "react";

import {
  applyDesktopTheme,
  desktopThemeOptions,
  persistDesktopTheme,
  readDesktopTheme,
  type DesktopThemeId,
} from "../theme";
import "./theme-tone-switcher.css";

/** Compact global tone control; the selected theme is persisted locally per browser. */
export function ThemeToneSwitcher() {
  const [themeId, setThemeId] = useState<DesktopThemeId>(() => readDesktopTheme());

  useEffect(() => {
    applyDesktopTheme(document.documentElement, themeId);
    persistDesktopTheme(themeId);
  }, [themeId]);

  return (
    <div className="theme-tone-switcher" role="group" aria-label="界面色调">
      <span className="theme-tone-switcher__label">配色</span>
      <div className="theme-tone-switcher__swatches">
        {desktopThemeOptions.map((option) => {
          const selected = option.id === themeId;
          return (
            <button
              type="button"
              className={`theme-tone-switcher__button${selected ? " is-active" : ""}`}
              aria-label={`${option.label}主题，${option.description}`}
              aria-pressed={selected}
              title={`${option.label} · ${option.description}`}
              key={option.id}
              onClick={() => setThemeId(option.id)}
            >
              <span
                className="theme-tone-switcher__swatch"
                style={{
                  background: `linear-gradient(135deg, ${option.swatches[0]} 0 42%, ${option.swatches[1]} 42% 72%, ${option.swatches[2]} 72% 100%)`,
                }}
                aria-hidden="true"
              />
              <span className="theme-tone-switcher__name">{option.label}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
