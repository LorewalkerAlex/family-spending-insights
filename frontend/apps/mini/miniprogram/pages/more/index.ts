import {
  applyMiniThemeChrome,
  isMiniThemeId,
  miniThemeOptions,
  persistMiniTheme,
  readMiniTheme,
  type MiniThemeId,
} from "../../theme/index";

interface MorePageData extends Record<string, unknown> {
  themeId: MiniThemeId;
  themeOptions: typeof miniThemeOptions;
}

interface MorePageContext {
  setData(data: Partial<MorePageData>): void;
}

interface ThemeTapEvent {
  currentTarget: {
    dataset: {
      theme?: unknown;
    };
  };
}

Page({
  data: {
    themeId: readMiniTheme(),
    themeOptions: miniThemeOptions,
  } satisfies MorePageData,

  onShow(this: MorePageContext) {
    const themeId = readMiniTheme();
    this.setData({ themeId });
    applyMiniThemeChrome(themeId);
  },

  onSelectTheme(this: MorePageContext, event: ThemeTapEvent) {
    const value = event.currentTarget.dataset.theme;
    if (!isMiniThemeId(value)) {
      return;
    }
    persistMiniTheme(value);
    this.setData({ themeId: value });
    applyMiniThemeChrome(value);
  },

  onTapScheduled() {
    wx.navigateTo({ url: "/pages/scheduled/index" });
  },

  onTapFeedback() {
    wx.navigateTo({ url: "/pages/feedback/index" });
  },
});