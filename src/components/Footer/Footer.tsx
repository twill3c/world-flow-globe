/**
 * フリート共通フッタ(5 項目・下部固定)。
 *
 * 並びと項目数は規約で決まっている。ラベルは各アプリの和名+固有動詞を温存する。
 */

/* fleet: fixed footer */

export const REPO_URL = "https://github.com/twill3c/world-flow-globe";

/** 解説アーティファクト。**公開したら URL を差し替える**(loop_008)。 */
export const GUIDE_URL = `${REPO_URL}/blob/main/README.md`;
export const DESIGN_URL = `${REPO_URL}/blob/main/SPEC.md`;

export default function Footer() {
  return (
    <footer className="fleet-footer">
      <a href={`${REPO_URL}/blob/main/LICENSE`} target="_blank" rel="noreferrer noopener">
        MIT License © 2026 坂田哲朗
      </a>
      <span aria-hidden="true">・</span>
      <a href={REPO_URL} target="_blank" rel="noreferrer noopener">
        GitHub
      </a>
      <span aria-hidden="true">・</span>
      <a href={GUIDE_URL} target="_blank" rel="noreferrer noopener">
        地球儀の回し方
      </a>
      <span aria-hidden="true">・</span>
      <a href={DESIGN_URL} target="_blank" rel="noreferrer noopener">
        World Flow Globe の設計図
      </a>
      <span aria-hidden="true">・</span>
      <a href="https://app-menu-amber.vercel.app/" target="_blank" rel="noreferrer noopener">
        App Menu
      </a>
    </footer>
  );
}
