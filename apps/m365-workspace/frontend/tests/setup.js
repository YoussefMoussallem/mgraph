// Vitest setup — runs before every test file.

// React requires this flag when act() is used outside React's own test
// renderers; without it every act() call warns and state updates may not
// flush synchronously.
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

// Node ≥22 ships an experimental `localStorage` global that shadows
// jsdom's and is non-functional unless Node was started with
// --localstorage-file. Replace it with a real in-memory implementation
// so tests that touch storage behave like a browser.
if (typeof globalThis.localStorage?.setItem !== "function") {
  const store = new Map();
  const memoryStorage = {
    get length() {
      return store.size;
    },
    key(index) {
      return [...store.keys()][index] ?? null;
    },
    getItem(key) {
      return store.has(String(key)) ? store.get(String(key)) : null;
    },
    setItem(key, value) {
      store.set(String(key), String(value));
    },
    removeItem(key) {
      store.delete(String(key));
    },
    clear() {
      store.clear();
    },
  };
  Object.defineProperty(globalThis, "localStorage", {
    value: memoryStorage,
    configurable: true,
    writable: true,
  });
}
