"use client";

import { useEffect, useRef } from "react";

type Props = {
  className?: string;
  /** Horizontal position of the main wave packet, from 0 (left) to 1 (right). */
  center?: number;
  lines?: number;
  /** Multiplies line brightness. */
  intensity?: number;
};

const BACKGROUND = "#ffffff";
const LINE_COLORS = [
  [95, 126, 174],
  [135, 116, 174],
  [166, 121, 145],
  [88, 137, 142],
  [101, 130, 171],
];

// Face the surface head-on so the ridge reads like a skyline, seen from a low angle.
const YAW = 0;
const PITCH = 0.62;

type Ripple = {
  x: number;
  z: number;
  born: number;
  mode: number;
  cos: number;
  sin: number;
  stretch: number;
  frequency: number;
  amplitude: number;
  phase: number;
  spread: number;
  strength: number;
};

/**
 * A perspective-projected surface with a bounded wake of randomized rings, saddles, and
 * crossing waves. Disturbances live in surface coordinates and fade after the cursor leaves.
 * Draws back to front with faint colored contours and pointer parallax. Runs only on screen,
 * at 60 fps during interaction and 30 fps otherwise; reduced motion gets one still frame.
 */
export function WaveField({ className, center = 0.66, lines = 34, intensity = 1 }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const context = canvas?.getContext("2d");
    if (!canvas || !context) return;

    const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let width = 0;
    let height = 0;
    let frame = 0;
    let lastDrawn = -Infinity;
    let onScreen = true;

    // The mesh is extended on resize until it spans the canvas. Outside the wave packet the
    // surface is nearly flat, so it is sampled four times more coarsely there. Its reach is
    // capped, which keeps sampling and memory bounded on very wide screens.
    const spacing = 5.6 / 128;
    const step = spacing * 8;
    const reach = 10;
    const maxStride = 129 + 2 * Math.ceil((reach - 2.8) / (spacing * 4));
    let segments = 128;
    let stride = segments + 1;
    let pointCount = lines * stride;
    const worldX = new Float32Array(lines * maxStride);
    const worldZ = new Float32Array(lines * maxStride);
    const envelope = new Float32Array(lines * maxStride);
    const elevation = new Float32Array(lines * maxStride);
    const screenX = new Float32Array(lines * maxStride);
    const screenY = new Float32Array(lines * maxStride);
    const crossColumn = new Uint8Array(maxStride);
    const colors: string[] = [];
    // Solid strokes rasterize far faster than gradients; the canvas mask fades them sideways.
    const strokes: string[] = [];
    const crossStrokes: string[] = [];

    for (let row = 0; row < lines; row++) {
      const depth = row / (lines - 1);
      const colorPosition = depth * (LINE_COLORS.length - 1);
      const colorIndex = Math.min(LINE_COLORS.length - 2, Math.floor(colorPosition));
      const blend = colorPosition - colorIndex;
      colors.push(LINE_COLORS[colorIndex].map((value, channel) => (
        Math.round(value + (LINE_COLORS[colorIndex + 1][channel] - value) * blend)
      )).join(","));
      const alpha = Math.min(1, (0.1 + 0.34 * depth) * intensity);
      strokes.push(`rgba(${colors[row]},${alpha})`);
      crossStrokes.push(`rgba(${colors[row]},${alpha * 0.24})`);
    }

    const unitFor = () => Math.min(width * 0.24, height * 0.36);

    // Screen x of a flat surface point at the resting camera angle, before pointer parallax.
    const restingX = (x: number, z: number) => {
      const away = x * Math.sin(YAW) + z * Math.cos(YAW);
      const side = x * Math.cos(YAW) - z * Math.sin(YAW);
      return width * center + side * unitFor() * 5 / (5 + away * Math.cos(PITCH));
    };

    // Grow each side in whole cross-contour steps so the original grid stays in place.
    const layout = () => {
      const margin = width * 0.08;
      const covers = (x: number, past: (screen: number) => boolean) => past(restingX(x, 1.25)) && past(restingX(x, -1.25));
      let left = 0;
      while (-2.8 - left * step > -reach && !covers(-2.8 - left * step, (screen) => screen < -margin)) left++;
      let right = 0;
      while (2.8 + right * step < reach && !covers(2.8 + right * step, (screen) => screen > width + margin)) right++;
      // Coarse columns every half step on the sides, the original fine columns in the middle.
      segments = 2 * left + 128 + 2 * right;
      stride = segments + 1;
      pointCount = lines * stride;
      for (let column = 0; column <= segments; column++) {
        const fine = column - 2 * left;
        const x = fine < 0
          ? -2.8 + fine * step / 2
          : fine > 128 ? 2.8 + (fine - 128) * step / 2 : -2.8 + fine * spacing;
        crossColumn[column] = (fine < 0 || fine > 128 ? fine % 2 : fine % 8) === 0 ? 1 : 0;
        for (let row = 0; row < lines; row++) {
          const index = row * stride + column;
          const z = 1.25 - (row / (lines - 1)) * 2.5;
          worldX[index] = x;
          worldZ[index] = z;
          // A steep rise on the left and a long slope to the right, like a mountain ridge.
          envelope[index] = Math.exp(-(x < 0 ? 0.65 : 0.22) * x * x - 0.4 * z * z);
        }
      }
    };

    const pointer = { clientX: 0, clientY: 0, known: false };
    const focus = { x: 0, z: 0, presence: 0 };
    const orbit = { x: 0, y: 0 };
    const ripples: Ripple[] = [];
    const rippleLifetime = 2.8;
    let lastRippleAt = -Infinity;

    const draw = (milliseconds: number) => {
      if (!width || !height) return;
      const t = milliseconds / 1000;
      const box = canvas.getBoundingClientRect();
      const pointerX = pointer.clientX - box.left;
      const pointerY = pointer.clientY - box.top;
      const inside = pointer.known && pointerX >= 0 && pointerX <= width && pointerY >= 0 && pointerY <= height;
      orbit.x += ((inside ? pointerX / width - 0.5 : 0) - orbit.x) * 0.04;
      orbit.y += ((inside ? pointerY / height - 0.5 : 0) - orbit.y) * 0.04;

      const yaw = YAW + orbit.x * 0.12;
      const pitch = PITCH + orbit.y * 0.08;
      const cosYaw = Math.cos(yaw);
      const sinYaw = Math.sin(yaw);
      const cosPitch = Math.cos(pitch);
      const sinPitch = Math.sin(pitch);
      const unit = unitFor();

      const project = (index: number, lift: number) => {
        const side = worldX[index] * cosYaw - worldZ[index] * sinYaw;
        const away = worldX[index] * sinYaw + worldZ[index] * cosYaw;
        const perspective = 5 / (5 + away * cosPitch - lift * sinPitch);
        screenX[index] = width * center + side * unit * perspective;
        screenY[index] = height * 0.65 - (away * sinPitch + lift * cosPitch) * unit * perspective;
      };

      let nearest = -1;
      let nearestDistance = (unit * 0.45) ** 2;
      for (let index = 0; index < pointCount; index++) {
        const x = worldX[index];
        const z = worldZ[index];
        elevation[index] = 0.05 + envelope[index] * (0.4 + 0.18 * Math.sin(x * 3.4 + z * 1.8 - t * 0.75))
          + 0.1 * Math.cos(z * 3.5 + x * 0.7 - t * 0.45)
          + 0.08 * Math.sin(x * 1.3 - z * 2.6 + t * 0.55);
        project(index, elevation[index]);
        // Pick on the undisturbed surface so the lifted peak cannot chase its own movement.
        if (inside) {
          const distance = (screenX[index] - pointerX) ** 2 + (screenY[index] - pointerY) ** 2;
          if (distance < nearestDistance) {
            nearest = index;
            nearestDistance = distance;
          }
        }
      }

      if (nearest >= 0) {
        const ease = focus.presence < 0.02 ? 1 : 0.16;
        focus.x += (worldX[nearest] - focus.x) * ease;
        focus.z += (worldZ[nearest] - focus.z) * ease;
      }
      focus.presence += ((nearest >= 0 ? 1 : 0) - focus.presence) * 0.09;

      // Emit at most twice a second, including when hovering in place. Each wake gets a
      // different orientation and shape without changing any existing wave abruptly.
      if (nearest >= 0 && t - lastRippleAt >= 0.5) {
        const angle = Math.random() * Math.PI * 2;
        if (ripples.length === 5) ripples.shift();
        ripples.push({
          x: focus.x + (Math.random() - 0.5) * 0.18,
          z: focus.z + (Math.random() - 0.5) * 0.18,
          born: t,
          mode: Math.floor(Math.random() * 3),
          cos: Math.cos(angle),
          sin: Math.sin(angle),
          stretch: 0.7 + Math.random() * 0.6,
          frequency: 7 + Math.random() * 4,
          amplitude: 0.4 + Math.random() * 0.16,
          phase: 0,
          spread: 0,
          strength: 0,
        });
        lastRippleAt = t;
      }

      for (let index = ripples.length - 1; index >= 0; index--) {
        const ripple = ripples[index];
        const age = t - ripple.born;
        if (age >= rippleLifetime) {
          ripples.splice(index, 1);
          continue;
        }
        ripple.phase = age * 3.8;
        ripple.spread = 0.22 + age * 0.3;
        ripple.strength = ripple.amplitude * (1 - Math.exp(-age * 8)) * (1 - age / rippleLifetime) ** 2;
      }

      if (ripples.length > 0) {
        for (let index = 0; index < pointCount; index++) {
          let lift = 0;
          for (const ripple of ripples) {
            const dx = worldX[index] - ripple.x;
            const dz = worldZ[index] - ripple.z;
            const u = dx * ripple.cos - dz * ripple.sin;
            const v = (dx * ripple.sin + dz * ripple.cos) * ripple.stretch;
            const distance = u * u + v * v;
            if (distance > ripple.spread * 10) continue;
            const falloff = Math.exp(-distance / ripple.spread);
            const ring = Math.cos(Math.sqrt(distance) * ripple.frequency - ripple.phase);
            const shape = ripple.mode === 0
              ? ring
              : ripple.mode === 1
                ? ring * (u * u - v * v) / (distance + 0.035)
                : Math.sin(u * ripple.frequency + ripple.phase * 0.7) * Math.cos(v * ripple.frequency * 0.75 - ripple.phase * 0.5);
            lift += ripple.strength * falloff * shape;
          }
          project(index, elevation[index] + 0.8 * Math.tanh(lift / 0.8));
        }
      }

      context.fillStyle = BACKGROUND;
      context.fillRect(0, 0, width, height);
      for (let row = 0; row < lines; row++) {
        const start = row * stride;
        const depth = row / (lines - 1);

        if (row > 0) {
          const previous = start - stride;
          context.beginPath();
          context.moveTo(screenX[previous], screenY[previous]);
          for (let column = 1; column <= segments; column++) {
            context.lineTo(screenX[previous + column], screenY[previous + column]);
          }
          for (let column = segments; column >= 0; column--) {
            context.lineTo(screenX[start + column], screenY[start + column]);
          }
          context.closePath();
          context.fillStyle = BACKGROUND;
          context.fill();

          // Cross-contours recede with the same perspective as the wave rows.
          context.beginPath();
          for (let column = 0; column <= segments; column++) {
            if (!crossColumn[column]) continue;
            context.moveTo(screenX[previous + column], screenY[previous + column]);
            context.lineTo(screenX[start + column], screenY[start + column]);
          }
          context.strokeStyle = crossStrokes[row];
          context.lineWidth = 0.7;
          context.stroke();
        }

        context.beginPath();
        context.moveTo(screenX[start], screenY[start]);
        for (let column = 1; column <= segments; column++) {
          context.lineTo(screenX[start + column], screenY[start + column]);
        }
        context.strokeStyle = strokes[row];
        context.lineWidth = 0.7 + depth * 0.5;
        context.stroke();
      }
    };

    const tick = (milliseconds: number) => {
      frame = requestAnimationFrame(tick);
      const interval = focus.presence > 0.02 || ripples.length > 0 ? 1000 / 60 : 1000 / 30;
      if (milliseconds - lastDrawn < interval - 1) return;
      lastDrawn = milliseconds;
      draw(milliseconds);
    };

    const start = () => {
      cancelAnimationFrame(frame);
      if (still) draw(24_000);
      else if (onScreen && !document.hidden) frame = requestAnimationFrame(tick);
    };

    const resize = () => {
      const ratio = Math.min(window.devicePixelRatio || 1, 1.5);
      width = canvas.clientWidth;
      height = canvas.clientHeight;
      canvas.width = Math.round(width * ratio);
      canvas.height = Math.round(height * ratio);
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
      layout();
      draw(still ? 24_000 : performance.now());
    };

    const move = (event: PointerEvent) => {
      if (event.pointerType !== "mouse" && event.pointerType !== "pen") return;
      pointer.clientX = event.clientX;
      pointer.clientY = event.clientY;
      pointer.known = true;
    };
    const leave = () => {
      pointer.known = false;
    };
    // Scrolling moves the surface under a still cursor; ignore it until the cursor moves again,
    // so scrolling never triggers ripples or the 60 fps interaction rate.
    const scroll = () => {
      pointer.known = false;
    };

    const sizeWatcher = new ResizeObserver(resize);
    sizeWatcher.observe(canvas);
    const screenWatcher = new IntersectionObserver(([entry]) => {
      onScreen = entry.isIntersecting;
      if (onScreen) start();
      else cancelAnimationFrame(frame);
    });
    screenWatcher.observe(canvas);
    const visibility = () => (document.hidden ? cancelAnimationFrame(frame) : start());
    document.addEventListener("visibilitychange", visibility);
    if (!still) {
      window.addEventListener("pointermove", move, { passive: true });
      window.addEventListener("scroll", scroll, { passive: true });
      document.documentElement.addEventListener("pointerleave", leave);
    }
    resize();
    start();

    return () => {
      cancelAnimationFrame(frame);
      sizeWatcher.disconnect();
      screenWatcher.disconnect();
      document.removeEventListener("visibilitychange", visibility);
      window.removeEventListener("pointermove", move);
      window.removeEventListener("scroll", scroll);
      document.documentElement.removeEventListener("pointerleave", leave);
    };
  }, [center, lines, intensity]);

  const peak = `${Math.min(0.95, Math.max(0.3, center)) * 100}%`;
  const fade = `linear-gradient(to right, rgb(0 0 0 / 0.45), black ${peak}, rgb(0 0 0 / 0.45))`;
  return <canvas ref={canvasRef} className={className} style={{ maskImage: fade, WebkitMaskImage: fade }} aria-hidden />;
}
