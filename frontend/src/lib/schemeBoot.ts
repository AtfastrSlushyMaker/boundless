export const SCHEME_KEY = "boundless:scheme";

/** Runs before first paint (inlined in the root layout) so the page never flashes the wrong scheme. */
export const SCHEME_BOOT_SCRIPT = `(function(){try{var p=localStorage.getItem("${SCHEME_KEY}")||"system";var d=p==="system"?(matchMedia("(prefers-color-scheme: light)").matches?"light":"dark"):p;document.documentElement.dataset.scheme=d;}catch(e){document.documentElement.dataset.scheme="dark";}})();`;
