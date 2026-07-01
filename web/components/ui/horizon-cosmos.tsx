"use client";

// HorizonCosmos — the scroll-scrubbed cosmic backdrop for the Horizon hero: three rotating
// grayscale star layers, a tamed nebula, an atmospheric rim glow, and four ridge silhouettes,
// composited through an UnrealBloom pass so the bright points read as silver embers on true black.
//
// It is the same scene family as the container-scoped HeroCanvas
// (components/ui/horizon-hero-section.tsx) with the two deltas the Horizon design needs (README §10):
//   1. Ridge silhouettes ("mountains") framing the lower third — buildMountains().
//   2. A scroll-scrubbed camera instead of pointer parallax: HorizonHero computes progress from the
//      hero region's OWN rect and writes the eased camera TARGET into a shared ref; this loop smooths
//      the live camera toward it each frame (never snaps) plus a gentle idle float, then looks at a
//      fixed point ahead. All exact numbers (camera, nebula tame, bloom threshold, fog) come from the
//      design prototype.
//
// Loaded via next/dynamic({ ssr:false }) by HorizonHero so three.js never enters the server bundle and
// the hero's text/LCP paints before the canvas mounts. By product decision this ALWAYS animates — no
// reduced-motion fallback — but it degrades to the instant-paint backdrop if WebGL is unavailable,
// caps pixel ratio / star count on small screens, pauses while off-screen or in a hidden tab, survives
// context loss, and disposes every GPU resource on unmount.

import { useEffect, useRef } from "react";
import type { MutableRefObject } from "react";
import * as THREE from "three";
import { EffectComposer } from "three/examples/jsm/postprocessing/EffectComposer.js";
import { RenderPass } from "three/examples/jsm/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/examples/jsm/postprocessing/UnrealBloomPass.js";

/** The camera position the scroll scrub flies toward — written by HorizonHero, read here per frame. */
export type CamTarget = { x: number; y: number; z: number };

type Refs = {
  renderer: THREE.WebGLRenderer | null;
  composer: EffectComposer | null;
  bloom: UnrealBloomPass | null;
  scene: THREE.Scene | null;
  camera: THREE.PerspectiveCamera | null;
  stars: THREE.Points[];
  nebula: THREE.Mesh | null;
  atmosphere: THREE.Mesh | null;
  mountains: THREE.Mesh[];
  animationId: number | null;
};

export function HorizonCosmos({
  camTargetRef,
  className,
}: {
  camTargetRef: MutableRefObject<CamTarget>;
  className?: string;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  // The live (smoothed) camera position — eased toward camTargetRef.current each frame.
  const cam = useRef<CamTarget>({ ...camTargetRef.current });
  const refs = useRef<Refs>({
    renderer: null,
    composer: null,
    bloom: null,
    scene: null,
    camera: null,
    stars: [],
    nebula: null,
    atmosphere: null,
    mountains: [],
    animationId: null,
  });

  useEffect(() => {
    const container = containerRef.current;
    const canvas = canvasRef.current;
    if (!container || !canvas) return;

    const size = () => ({
      w: container.clientWidth || window.innerWidth,
      h: container.clientHeight || window.innerHeight,
    });

    let { w, h } = size();
    // Fewer points + tighter pixel ratio on small / dense screens — the bloom carries the look,
    // so a thinner field still reads full while keeping mobile GPUs comfortable.
    const isSmall = w < 768;
    const starCount = isSmall ? 1600 : 4200;
    // Cap the pixel ratio hard: the full-screen bloom pass is fragment-bound, so drawing at native
    // 2× DPR is what makes the scrub feel heavy. 1.5× (1× on small screens) is visually the same once
    // bloom softens the field, and roughly halves the fill cost.
    //
    // Then bound the ABSOLUTE drawing-buffer width on top of that. Every pixel of this scene is paid for
    // more than once a frame — by the full-screen additive passes (nebula, atmosphere, stars) AND by
    // bloom's multi-tap blur — so on a large / hidpi / ultrawide panel the raw pixel count is the single
    // biggest reason the scrub feels heavy. Capping the buffer to ~1600px wide costs nothing visible
    // (bloom is inherently blurry, the ridges hide behind the vignette + bottom-fade, and the title is
    // crisp DOM text never drawn to this canvas) while cutting the per-frame fill 2–3× on the screens
    // that felt worst. This also gently downscales a plain 1920×1080 DPR-1 desktop to 1600 — intended.
    // Width-dependent, so it's a function: onResize re-derives it (a widen-after-mount must re-tighten
    // the cap or the buffer would drift back over 1600).
    const MAX_BUFFER_W = 1600;
    const pixelRatioFor = (width: number) =>
      Math.min(window.devicePixelRatio || 1, width < 768 ? 1 : 1.5, MAX_BUFFER_W / width);
    let pixelRatio = pixelRatioFor(w);

    const r = refs.current;

    // ── scene + camera ──────────────────────────────────────────────────────
    r.scene = new THREE.Scene();
    r.scene.fog = new THREE.FogExp2(0x05050a, 0.00028);

    r.camera = new THREE.PerspectiveCamera(75, w / h, 0.1, 2000);
    r.camera.position.set(camTargetRef.current.x, camTargetRef.current.y, camTargetRef.current.z);

    // ── renderer (guarded: no WebGL → bail to the instant-paint backdrop) ─────
    try {
      r.renderer = new THREE.WebGLRenderer({
        canvas,
        // No MSAA and no preserved buffer — the two biggest per-frame costs here, and both
        // unnecessary: the stars are soft fragment-shaded sprites (bloom hides any ridge aliasing),
        // and the loop runs continuously while the hero is on screen, so there's no paused frame to
        // retain — while preserveDrawingBuffer forces a slow buffer copy every frame. Dropping them
        // is the main fix for the scrub jank.
        antialias: false,
        alpha: true,
        powerPreference: "high-performance",
      });
    } catch {
      return;
    }
    r.renderer.setSize(w, h, false);
    r.renderer.setPixelRatio(pixelRatio);
    r.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    r.renderer.toneMappingExposure = 0.62;

    // ── post: bloom turns only the bright stars into silver halos ─────────────
    // Threshold 0.7 (vs HeroCanvas's 0.6) keeps the nebula from washing the centered title.
    r.composer = new EffectComposer(r.renderer);
    r.composer.setSize(w, h);
    r.composer.addPass(new RenderPass(r.scene, r.camera));
    // Size the bloom blur to the CAPPED buffer, not raw CSS px. UnrealBloomPass builds its mip-blur
    // pyramid from the resolution it's handed, and the composer.setSize() above ran before this pass was
    // added — so left at CSS px the dominant per-frame cost would keep running at full resolution and the
    // buffer cap would never reach the very thing it most needs to. w*pixelRatio is ≤ MAX_BUFFER_W by
    // construction, so the halos look identical while the blur gets materially cheaper on big screens.
    r.bloom = new UnrealBloomPass(
      new THREE.Vector2(Math.round(w * pixelRatio), Math.round(h * pixelRatio)),
      0.6,
      0.5,
      0.7,
    );
    r.composer.addPass(r.bloom);

    // ── starfield: three depth layers, each rotating at its own slow rate ─────
    const starVertex = `
      attribute float size;
      attribute vec3 color;
      varying vec3 vColor;
      uniform float time;
      uniform float depth;
      void main() {
        vColor = color;
        vec3 pos = position;
        float angle = time * 0.04 * (1.0 - depth * 0.3);
        mat2 rot = mat2(cos(angle), -sin(angle), sin(angle), cos(angle));
        pos.xy = rot * pos.xy;
        vec4 mvPosition = modelViewMatrix * vec4(pos, 1.0);
        // Clamp the sprite size. As the camera flies THROUGH the field, a star a few units ahead would
        // otherwise swell to hundreds of px and — being additively blended — repaint a huge slice of the
        // screen every frame: the overdraw spike that makes the flythrough hitch right when it should
        // feel smoothest. 64px preserves the "swell past a star" read while bounding the worst-case fill.
        gl_PointSize = min(size * (300.0 / -mvPosition.z), 64.0);
        gl_Position = projectionMatrix * mvPosition;
      }
    `;
    const starFragment = `
      varying vec3 vColor;
      void main() {
        float dist = length(gl_PointCoord - vec2(0.5));
        if (dist > 0.5) discard;
        float opacity = 1.0 - smoothstep(0.0, 0.5, dist);
        gl_FragColor = vec4(vColor, opacity);
      }
    `;
    for (let layer = 0; layer < 3; layer++) {
      const geometry = new THREE.BufferGeometry();
      const positions = new Float32Array(starCount * 3);
      const colors = new Float32Array(starCount * 3);
      const sizes = new Float32Array(starCount);

      for (let j = 0; j < starCount; j++) {
        const radius = 200 + Math.random() * 800;
        const theta = Math.random() * Math.PI * 2;
        const phi = Math.acos(Math.random() * 2 - 1);
        positions[j * 3] = radius * Math.sin(phi) * Math.cos(theta);
        positions[j * 3 + 1] = radius * Math.sin(phi) * Math.sin(theta);
        positions[j * 3 + 2] = radius * Math.cos(phi);

        // Monochrome palette: bright whites with a scatter of dimmer silvers.
        const color = new THREE.Color();
        const pick = Math.random();
        if (pick < 0.7) color.setHSL(0, 0, 0.9 + Math.random() * 0.1);
        else if (pick < 0.92) color.setHSL(0, 0, 0.74);
        else color.setHSL(0, 0, 0.6);
        colors[j * 3] = color.r;
        colors[j * 3 + 1] = color.g;
        colors[j * 3 + 2] = color.b;
        sizes[j] = Math.random() * 2 + 0.5;
      }

      geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
      geometry.setAttribute("color", new THREE.BufferAttribute(colors, 3));
      geometry.setAttribute("size", new THREE.BufferAttribute(sizes, 1));

      const material = new THREE.ShaderMaterial({
        uniforms: { time: { value: 0 }, depth: { value: layer } },
        vertexShader: starVertex,
        fragmentShader: starFragment,
        transparent: true,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
      });

      const stars = new THREE.Points(geometry, material);
      r.scene.add(stars);
      r.stars.push(stars);
    }

    // ── nebula: a slow grayscale wash, tamed so it never blows out behind the title ──
    {
      const geometry = new THREE.PlaneGeometry(8000, 4000, 80, 80);
      const material = new THREE.ShaderMaterial({
        uniforms: {
          time: { value: 0 },
          color1: { value: new THREE.Color(0x6e6e76) },
          color2: { value: new THREE.Color(0x131316) },
          opacity: { value: 0.09 },
        },
        vertexShader: `
          varying vec2 vUv;
          varying float vElevation;
          uniform float time;
          void main() {
            vUv = uv;
            vec3 pos = position;
            float elevation = sin(pos.x * 0.01 + time) * cos(pos.y * 0.01 + time) * 18.0;
            pos.z += elevation;
            vElevation = elevation;
            gl_Position = projectionMatrix * modelViewMatrix * vec4(pos, 1.0);
          }
        `,
        fragmentShader: `
          uniform vec3 color1;
          uniform vec3 color2;
          uniform float opacity;
          uniform float time;
          varying vec2 vUv;
          varying float vElevation;
          void main() {
            float mixFactor = sin(vUv.x * 10.0 + time) * cos(vUv.y * 10.0 + time);
            vec3 color = mix(color1, color2, mixFactor * 0.5 + 0.5);
            float alpha = opacity * (1.0 - length(vUv - 0.5) * 2.0);
            alpha *= 1.0 + vElevation * 0.01;
            gl_FragColor = vec4(color, alpha);
          }
        `,
        transparent: true,
        blending: THREE.AdditiveBlending,
        side: THREE.DoubleSide,
        depthWrite: false,
      });
      const nebula = new THREE.Mesh(geometry, material);
      nebula.position.z = -1050;
      r.scene.add(nebula);
      r.nebula = nebula;
    }

    // ── atmosphere: a faint cool-white rim that pulses around the camera ───────
    {
      const geometry = new THREE.SphereGeometry(600, 32, 32);
      const material = new THREE.ShaderMaterial({
        uniforms: { time: { value: 0 } },
        vertexShader: `
          varying vec3 vNormal;
          void main() {
            vNormal = normalize(normalMatrix * normal);
            gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
          }
        `,
        fragmentShader: `
          varying vec3 vNormal;
          uniform float time;
          void main() {
            float intensity = pow(0.7 - dot(vNormal, vec3(0.0, 0.0, 1.0)), 2.0);
            vec3 glow = vec3(0.82, 0.84, 0.9) * intensity;
            float pulse = sin(time * 1.6) * 0.1 + 0.9;
            gl_FragColor = vec4(glow * pulse, intensity * 0.22);
          }
        `,
        side: THREE.BackSide,
        blending: THREE.AdditiveBlending,
        transparent: true,
      });
      const atmosphere = new THREE.Mesh(geometry, material);
      r.scene.add(atmosphere);
      r.atmosphere = atmosphere;
    }

    // ── ridge silhouettes: four layered horizon shapes framing the lower third ──
    {
      const layers = [
        { z: -50, height: 60, color: 0x131316, opacity: 1.0 },
        { z: -100, height: 84, color: 0x191920, opacity: 0.82 },
        { z: -150, height: 108, color: 0x202028, opacity: 0.6 },
        { z: -200, height: 132, color: 0x282830, opacity: 0.42 },
      ];
      for (const L of layers) {
        const pts: THREE.Vector2[] = [];
        const seg = 60;
        for (let i = 0; i <= seg; i++) {
          const x = (i / seg - 0.5) * 1500;
          const y =
            Math.sin(i * 0.1) * L.height +
            Math.sin(i * 0.05) * L.height * 0.5 +
            Math.random() * L.height * 0.2 -
            70;
          pts.push(new THREE.Vector2(x, y));
        }
        // Close the shape well below the frame so the silhouette fills the bottom edge.
        pts.push(new THREE.Vector2(7000, -600));
        pts.push(new THREE.Vector2(-7000, -600));
        const shape = new THREE.Shape(pts);
        const geometry = new THREE.ShapeGeometry(shape);
        const material = new THREE.MeshBasicMaterial({
          color: L.color,
          transparent: true,
          opacity: L.opacity,
          side: THREE.DoubleSide,
        });
        const mesh = new THREE.Mesh(geometry, material);
        mesh.position.z = L.z;
        mesh.position.y = -34;
        r.scene.add(mesh);
        r.mountains.push(mesh);
      }
    }

    // ── render ────────────────────────────────────────────────────────────
    // Manual elapsed-time clock — THREE.Clock is deprecated as of three r185.
    const t0 = performance.now();
    let lastRenderAt = t0;
    let faded = false;

    const renderFrame = () => {
      const now = performance.now();
      const t = (now - t0) / 1000;
      // Real seconds since the previous DRAW (clamped so a resumed pause can't produce a huge jump).
      // Used to make the camera smoothing frame-rate independent below.
      const dt = Math.min((now - lastRenderAt) / 1000, 0.1);
      lastRenderAt = now;
      for (const s of r.stars) {
        const u = (s.material as THREE.ShaderMaterial).uniforms;
        if (u?.time) u.time.value = t;
      }
      if (r.nebula) {
        const u = (r.nebula.material as THREE.ShaderMaterial).uniforms;
        if (u?.time) u.time.value = t * 0.5;
      }
      if (r.atmosphere) {
        const u = (r.atmosphere.material as THREE.ShaderMaterial).uniforms;
        if (u?.time) u.time.value = t;
      }
      if (r.camera) {
        // Smooth the live camera toward the scroll-scrubbed target (never snap), then add a
        // gentle autonomous float and look at a fixed point ahead.
        // Follow strength is tuned as 0.1-per-frame AT 60fps (tighter than the original 0.06 so the
        // flythrough tracks the scroll instead of visibly trailing it). But a fixed per-frame lerp
        // trails further the fewer frames we draw — which the ~72fps redraw cap below would otherwise
        // cause on high-refresh panels. Deriving the factor from real dt makes the follow-feel identical
        // at 60/72/120/144Hz: k solves (1-0.1)^(dt*60) so exactly 60fps reproduces the old 0.1 per frame.
        const tgt = camTargetRef.current;
        const k = 1 - Math.pow(0.9, dt * 60);
        cam.current.x += (tgt.x - cam.current.x) * k;
        cam.current.y += (tgt.y - cam.current.y) * k;
        cam.current.z += (tgt.z - cam.current.z) * k;
        r.camera.position.set(
          cam.current.x + Math.sin(t * 0.1) * 2,
          cam.current.y + Math.cos(t * 0.15) * 1.5,
          cam.current.z,
        );
        r.camera.lookAt(0, 10, -600);
      }
      // A subtle per-frame sway on the ridges, stronger on the nearer layers.
      for (let i = 0; i < r.mountains.length; i++) {
        r.mountains[i].position.x = Math.sin(t * 0.1) * 2 * (1 + i * 0.4);
      }
      r.composer?.render();

      // Reveal the canvas once the first frame is on screen. Set DIRECTLY (never gated behind a
      // document-timeline animation, which can freeze in a backgrounded tab); the CSS transition
      // on the element gives a soft 1.1s fade where the compositor is live. README §12 gotcha 3.
      if (!faded) {
        faded = true;
        canvas.style.opacity = "1";
      }
    };

    // Soft-cap the redraw. The whole scene is time-based, so on a 120/144Hz panel we were paying
    // 2–2.4× the GPU cost for motion the eye reads as identical — the quickest way to drop frames on a
    // bloom-heavy scene. Skipping any frame that arrives sooner than ~13.5ms after the last draw leaves a
    // 60Hz panel untouched (16.7ms > budget), settles a 120Hz panel to 60fps and a 144Hz panel to 72fps —
    // every case a clean win over running the full refresh, and none dips below 60. (A 60fps/16.7ms budget
    // would instead land 144Hz on 48fps — worse than before — because rAF only hits integer divisors of the
    // refresh.) The dt-scaled smoothing above keeps the follow-feel identical whatever rate this settles to.
    // Only the WebGL redraw is paced; the scroll-scrubbed camera TARGET still updates at full browser rate.
    const MIN_FRAME_MS = 13.5;
    let lastFrameAt = -Infinity;
    const loop = (now: number) => {
      r.animationId = requestAnimationFrame(loop);
      if (now - lastFrameAt < MIN_FRAME_MS) return;
      lastFrameAt = now;
      renderFrame();
    };
    const startLoop = () => {
      if (r.animationId == null) r.animationId = requestAnimationFrame(loop);
    };
    const stopLoop = () => {
      if (r.animationId != null) {
        cancelAnimationFrame(r.animationId);
        r.animationId = null;
      }
    };

    // The loop runs only while the stage is BOTH on-screen and in a visible tab.
    let inView = false;
    const evaluate = () => {
      if (inView && !document.hidden) startLoop();
      else stopLoop();
    };
    const io = new IntersectionObserver(
      ([entry]) => {
        inView = entry.isIntersecting;
        evaluate();
      },
      { threshold: 0.02 },
    );
    const onVisibility = () => evaluate();

    // While the loop is paused (hidden tab just revealed mid-scroll, throttled), a scroll still
    // needs to reflect the new camera target — render one frame on demand. During the actual scrub
    // the stage is pinned and in view, so the loop is already running and this is a cheap no-op.
    const onScroll = () => {
      if (r.animationId == null) renderFrame();
    };

    renderFrame(); // immediate first paint, then the observer starts the loop
    io.observe(container);
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("scroll", onScroll, { passive: true });

    // ── WebGL context loss: pause cleanly, resume when the GPU comes back ─────
    const onContextLost = (e: Event) => {
      e.preventDefault();
      stopLoop();
    };
    const onContextRestored = () => evaluate();
    canvas.addEventListener("webglcontextlost", onContextLost);
    canvas.addEventListener("webglcontextrestored", onContextRestored);

    // ── responsive: track the container (= the 100vh sticky stage), not the window ──
    const onResize = () => {
      const next = size();
      // Ignore degenerate mid-layout measurements (a 0–8px snapshot during a reflow) — applying
      // them would freeze the drawing buffer at a garbage resolution until the next change.
      if (next.w < 50 || next.h < 50) return;
      w = next.w;
      h = next.h;
      if (!r.camera || !r.renderer || !r.composer || !r.bloom) return;
      // Re-derive the width-dependent cap so widening the window (or dragging between monitors) can't let
      // the buffer drift back over 1600. Push it to BOTH the renderer and the composer so the RenderPass
      // targets and the bloom pyramid all track the capped resolution — then composer.setSize sizes bloom
      // for us (no manual r.bloom.setSize(w, h), which used raw CSS px and silently re-inflated the
      // dominant pass to full resolution after every resize).
      pixelRatio = pixelRatioFor(w);
      r.renderer.setPixelRatio(pixelRatio);
      r.composer.setPixelRatio(pixelRatio);
      r.camera.aspect = w / h;
      r.camera.updateProjectionMatrix();
      r.renderer.setSize(w, h, false);
      r.composer.setSize(w, h);
      if (r.animationId == null) renderFrame(); // refresh the frame while paused
    };
    const ro = new ResizeObserver(onResize);
    ro.observe(container);

    // ── teardown: stop the loop, drop listeners, free every GPU handle ───────
    return () => {
      stopLoop();
      io.disconnect();
      ro.disconnect();
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("scroll", onScroll);
      canvas.removeEventListener("webglcontextlost", onContextLost);
      canvas.removeEventListener("webglcontextrestored", onContextRestored);
      for (const s of r.stars) {
        s.geometry.dispose();
        (s.material as THREE.Material).dispose();
      }
      r.stars = [];
      for (const m of r.mountains) {
        m.geometry.dispose();
        (m.material as THREE.Material).dispose();
      }
      r.mountains = [];
      if (r.nebula) {
        r.nebula.geometry.dispose();
        (r.nebula.material as THREE.Material).dispose();
      }
      if (r.atmosphere) {
        r.atmosphere.geometry.dispose();
        (r.atmosphere.material as THREE.Material).dispose();
      }
      // Dispose each pass's render targets before the composer (composer.dispose() alone leaves
      // the bloom pass's targets allocated — a VRAM leak across remounts).
      r.composer?.passes?.forEach((p) => (p as { dispose?: () => void }).dispose?.());
      r.composer?.dispose();
      r.renderer?.dispose();
      r.scene = null;
      r.camera = null;
      r.renderer = null;
      r.composer = null;
      r.bloom = null;
      r.nebula = null;
      r.atmosphere = null;
    };
  }, [camTargetRef]);

  return (
    <div ref={containerRef} className={className} aria-hidden>
      {/* Opacity starts at 0 and is committed to 1 inline on the first rendered frame (above) —
          the transition only smooths it. If WebGL is unavailable the canvas stays transparent and
          the instant-paint backdrop shows through. */}
      <canvas
        ref={canvasRef}
        className="block h-full w-full"
        style={{ opacity: 0, transition: "opacity 1.1s ease" }}
      />
    </div>
  );
}

export default HorizonCosmos;
