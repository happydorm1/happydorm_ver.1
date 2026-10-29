import { useEffect, useState } from "react";

/**
 * 해시 라우터(#/loans/123?tab=rates) — 서버 설정 없이 새로고침·즐겨찾기·뒤로가기가 동작한다.
 */
export interface Route { path: string[]; query: URLSearchParams }

function parse(): Route {
  const raw = window.location.hash.replace(/^#\/?/, "");
  const [p, q] = raw.split("?");
  return { path: (p || "dashboard").split("/").filter(Boolean).map(decodeURIComponent), query: new URLSearchParams(q ?? "") };
}

export function useRoute(): Route {
  const [route, setRoute] = useState(parse);
  useEffect(() => {
    const on = () => { setRoute(parse()); window.scrollTo(0, 0); };
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return route;
}

export function go(path: string, query?: Record<string, string | undefined>) {
  const qs = new URLSearchParams();
  Object.entries(query ?? {}).forEach(([k, v]) => { if (v) qs.set(k, v); });
  const s = qs.toString();
  window.location.hash = `#/${path.replace(/^\//, "")}${s ? `?${s}` : ""}`;
}

export const href = (path: string, query?: Record<string, string>) =>
  `#/${path.replace(/^\//, "")}${query ? `?${new URLSearchParams(query)}` : ""}`;
