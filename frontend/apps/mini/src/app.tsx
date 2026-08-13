import type { PropsWithChildren } from "react";

import "./app.css";

/** Taro app root intentionally holds no financial state; pages read through the shared service layer. */
export default function App({ children }: PropsWithChildren) {
  return children;
}
