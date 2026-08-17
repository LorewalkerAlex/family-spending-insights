interface MiniProgramAccountInfo {
  miniProgram: {
    envVersion: "develop" | "trial" | "release";
  };
}

interface MiniProgramRequestOptions {
  url: string;
  method: "GET" | "POST" | "PATCH" | "DELETE";
  header: Record<string, string>;
  data?: unknown;
  success: (response: { statusCode: number; data: unknown }) => void;
  fail: (error: { errMsg: string }) => void;
}

interface MiniProgramSwitchTabOptions {
  url: string;
}

interface MiniProgramNavigateToOptions {
  url: string;
}

interface MiniProgramNavigateBackOptions {
  delta?: number;
}

interface MiniProgramNavigationBarColorOptions {
  frontColor: "#000000" | "#ffffff";
  backgroundColor: string;
}

interface MiniProgramTabBarStyleOptions {
  color: string;
  selectedColor: string;
  backgroundColor: string;
  borderStyle: "black" | "white";
}

declare const wx: {
  request(options: MiniProgramRequestOptions): unknown;
  getAccountInfoSync(): MiniProgramAccountInfo;
  getStorageSync(key: string): unknown;
  setStorageSync(key: string, value: unknown): void;
  setNavigationBarColor(options: MiniProgramNavigationBarColorOptions): unknown;
  setTabBarStyle(options: MiniProgramTabBarStyleOptions): unknown;
  stopPullDownRefresh(): void;
  switchTab(options: MiniProgramSwitchTabOptions): void;
  navigateTo(options: MiniProgramNavigateToOptions): void;
  navigateBack(options?: MiniProgramNavigateBackOptions): void;
};

declare function App(options: Record<string, unknown>): void;
declare function Page(options: Record<string, unknown>): void;