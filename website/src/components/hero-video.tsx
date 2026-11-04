"use client";

import Image from "next/image";
import { useEffect, useRef, useState } from "react";
import { ReplayIcon } from "@/components/icons";

// The video opens on 1.2s of a still window, the same as the poster, so it starts just before the
// first movement, leaving only enough time for the poster to fade out.
const START = 1.05;

const BUTTON = {
  waiting: "hidden motion-reduce:inline-flex",
  refused: "inline-flex",
  playing: "hidden",
  ended: "inline-flex",
};

// Plays once, when it fills 80% of the screen, or is centered if it's too short to. It loads on the
// first scroll, so it's ready to start at once by then, and visitors who never scroll don't pay for
// it. Visitors who asked for less motion keep the poster unless they press the button.
export function HeroVideo() {
  const video = useRef<HTMLVideoElement>(null);
  const [stage, setStage] = useState<keyof typeof BUTTON>("waiting");

  useEffect(() => {
    const element = video.current;
    if (!element || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const screen = window.innerHeight;
    // How far from the top of the screen the video's top edge starts it.
    const start = Math.max(screen * 0.2, (screen - element.getBoundingClientRect().height) / 2);
    const prepare = () => {
      element.preload = "auto";
      element.load();
    };
    window.addEventListener("scroll", prepare, { once: true, passive: true });
    const watcher = new IntersectionObserver(
      ([entry]) => {
        if (!entry.isIntersecting) return;
        watcher.disconnect();
        window.removeEventListener("scroll", prepare);
        // Low Power Mode refuses playback, which leaves the poster and the button.
        element.play().catch(() => setStage("refused"));
      },
      { rootMargin: `0px 0px ${start - screen}px 0px` },
    );
    watcher.observe(element);
    return () => {
      window.removeEventListener("scroll", prepare);
      watcher.disconnect();
    };
  }, []);

  const covered = stage === "waiting" || stage === "refused";
  return (
    <>
      <video
        ref={video}
        src={`/wavefunction-hero.mp4#t=${START}`}
        aria-label="Wavefunction on a Mac: a chat builds an iPhone app, takes a steering message while it works, starts helpers and opens a browser."
        width={2560}
        height={1388}
        preload="none"
        muted
        playsInline
        onPlaying={() => setStage("playing")}
        onEnded={() => setStage("ended")}
        className="hero-desktop-video h-auto"
      />
      {/* Over the video, not its poster: browsers draw a video's colors a little differently from
          an image's, so swapping one for the other flashes, and fading between them doesn't. */}
      <Image
        src="/wavefunction-hero-poster.jpg"
        alt=""
        width={2560}
        height={1388}
        sizes="(max-width: 639px) 125vw, 100vw"
        loading="eager"
        className={`hero-desktop-video absolute left-0 top-0 h-auto transition-opacity duration-150 ${covered ? "" : "opacity-0"}`}
      />
      {/* Under "What are we building?", where the video ends, and centered on phones. */}
      <button
        type="button"
        onClick={() => {
          if (!video.current) return;
          video.current.currentTime = START;
          video.current.play();
        }}
        className={`absolute left-[55.8%] top-[64%] h-9 -translate-x-1/2 items-center gap-2 rounded-[10px] bg-white px-3.5 text-sm font-medium text-text transition-colors hover:bg-white/85 max-sm:left-1/2 ${BUTTON[stage]}`}
      >
        <ReplayIcon />
        Replay
      </button>
    </>
  );
}
