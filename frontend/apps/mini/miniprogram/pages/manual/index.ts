import {
  applyMiniThemeChrome,
  readMiniTheme,
  type MiniThemeId,
} from "../../theme/index";

interface ThemedPageData extends Record<string, unknown> {
  themeId: MiniThemeId;
}

interface ThemedPageContext {
  setData(data: Partial<ThemedPageData>): void;
}

Page({
  data: {
    themeId: readMiniTheme(),
  } satisfies ThemedPageData,

  onShow(this: ThemedPageContext) {
    const themeId = readMiniTheme();
    this.setData({ themeId });
    applyMiniThemeChrome(themeId);
  },
});
