import { useEffect } from "react";

const STONE_950 = "#0c0a09";

/**
 * The wall is always dark, whatever the system theme. Match the document
 * behind it (overscroll areas, scrollbars, the mobile browser bar) while it is
 * shown, and put everything back afterwards.
 */
export function useDarkPageChrome(): void {
  useEffect(() => {
    const root = document.documentElement;
    const previous = { colorScheme: root.style.colorScheme, background: root.style.backgroundColor };
    root.style.colorScheme = "dark";
    root.style.backgroundColor = STONE_950;

    const metas = [...document.querySelectorAll<HTMLMetaElement>('meta[name="theme-color"]')];
    const contents = metas.map((meta) => meta.content);
    for (const meta of metas) meta.content = STONE_950;

    return () => {
      root.style.colorScheme = previous.colorScheme;
      root.style.backgroundColor = previous.background;
      metas.forEach((meta, index) => {
        meta.content = contents[index] ?? meta.content;
      });
    };
  }, []);
}

/** Start downloading images that are about to be shown. */
export function usePreloadImages(urls: readonly string[]): void {
  const key = urls.filter(Boolean).join("\n");
  useEffect(() => {
    if (!key) return;
    for (const url of key.split("\n")) {
      const image = new Image();
      image.decoding = "async";
      image.src = url;
    }
  }, [key]);
}
