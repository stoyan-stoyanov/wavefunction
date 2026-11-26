"use client";

import Script from "next/script";
import { useSyncExternalStore } from "react";

declare global {
  interface Window {
    gtag?: (...args: unknown[]) => void;
  }
}

// The Google tag for the Google Analytics property, owned by the project's own Google account.
const GOOGLE_TAG = "GTM-55MJR4N3";
const CHOICE_KEY = "wavefunction.analytics-consent";
// Visitors in the EU, EEA, UK and Switzerland must agree to analytics cookies first. The browser's
// time zone stands in for where they are, so the page can stay static.
const ASK_ZONES = /^(Europe\/|Atlantic\/(Reykjavik|Canary|Madeira|Azores|Faroe)|Asia\/(Nicosia|Famagusta))/;

type Consent = "ask" | "granted" | "denied";

const listeners = new Set<() => void>();

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function consent(): Consent {
  try {
    const choice = localStorage.getItem(CHOICE_KEY);
    if (choice === "granted" || choice === "denied") return choice;
    return ASK_ZONES.test(Intl.DateTimeFormat().resolvedOptions().timeZone ?? "") ? "ask" : "granted";
  } catch {
    // Storage blocked: no way to remember an answer, so don't ask and don't load the tag.
    return "denied";
  }
}

function useConsent(): Consent | null {
  // Null on the server and during hydration, so the static HTML never carries the tag or banner.
  return useSyncExternalStore(subscribe, consent, () => null);
}

function answer(choice: "granted" | "denied" | null) {
  try {
    if (choice) localStorage.setItem(CHOICE_KEY, choice);
    else localStorage.removeItem(CHOICE_KEY);
  } catch {}
  listeners.forEach((listener) => listener());
}

/** Loads Google Analytics only for visitors who don't need to be asked, or who said yes. */
export function GoogleTag() {
  if (useConsent() !== "granted") return null;
  return (
    <>
      <Script id="google-tag-init" strategy="afterInteractive">
        {"window.dataLayer=window.dataLayer||[];window.gtag=function(){dataLayer.push(arguments)};gtag('js',new Date());"}
      </Script>
      <Script src={`https://www.googletagmanager.com/gtm.js?id=${GOOGLE_TAG}`} strategy="afterInteractive" />
    </>
  );
}

export function ConsentBanner() {
  if (useConsent() !== "ask") return null;
  return (
    <div
      role="region"
      aria-label="Analytics cookies"
      className="fixed inset-x-4 bottom-4 z-50 mx-auto flex max-w-[36rem] flex-col gap-4 rounded-2xl bg-bg p-5 text-sm text-soft shadow-lg ring-1 ring-text/10 sm:flex-row sm:items-center"
    >
      <p className="flex-1">May we use Google Analytics cookies to see which pages bring visitors?</p>
      <div className="flex gap-2">
        <button
          type="button"
          onClick={() => answer("denied")}
          className="h-9 rounded-[10px] bg-panel px-3.5 font-medium text-text transition-colors hover:bg-panel-raised"
        >
          No thanks
        </button>
        <button
          type="button"
          onClick={() => answer("granted")}
          className="h-9 rounded-[10px] bg-text px-3.5 font-medium text-white transition-colors hover:bg-text/85"
        >
          Allow
        </button>
      </div>
    </div>
  );
}

/** Lets visitors who were asked change their answer. Reloads, so a loaded tag stops for good. */
export function CookieSettings({ className }: { className: string }) {
  const state = useConsent();
  if (state === null || !ASK_ZONES.test(Intl.DateTimeFormat().resolvedOptions().timeZone ?? "")) return null;
  return (
    <button
      type="button"
      onClick={() => {
        answer(null);
        location.reload();
      }}
      className={className}
    >
      Cookies
    </button>
  );
}
