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

declare const wx: {
  request(options: MiniProgramRequestOptions): unknown;
  getAccountInfoSync(): MiniProgramAccountInfo;
  stopPullDownRefresh(): void;
};

declare function App(options: Record<string, unknown>): void;
declare function Page(options: Record<string, unknown>): void;
