"use client";

// HeroCanvas — a container-scoped cosmic backdrop: a layered gold/white starfield, a
// drifting nebula, and an atmospheric rim glow, composited through an UnrealBloom pass
// so the bright points read as warm embers against true black. Adapted from a 21st.dev
// "horizon" hero, with three deliberate changes for this product:
//
//   1. Scoped, not page-hijacking. The original drove the camera off total document
//      scroll (and rendered to the full window), which would fight this site's wizard,
//      FAQ, and scroll-progress bar. Here the renderer sizes to its OWN container and the
//      only motion is a slow autonomous drift + a faint pointer parallax — no scroll trap.
//   2. Black & white. Star/nebula/atmosphere palettes are pure grayscale (white → silver),
//      tuned to the app's accent so the hero and the brand read as one thing.
//   3. Always-on + lean. The full animation plays for every visitor (reduced-motion is
//      intentionally NOT honored here — product decision: this is the signature moment).
//      It still degrades to the CSS fallback if WebGL is unavailable, caps pixel ratio /
//      star count on small screens, pauses while off-screen, and disposes every GPU
//      resource on unmount. Loaded via next/dynamic({ ssr:false }) by the Hero, so
//      three.js never enters the server bundle and the text/LCP paints before it mounts.

import { useEffect, useRef } from "react";
import * as THREE from "three";
import { EffectComposer } from "three/examples/jsm/postprocessing/EffectComposer.js";
import { RenderPass } from "three/examples/jsm/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/examples/jsm/postprocessing/UnrealBloomPass.js";

type Refs = {
  scene: THREE.Scene | null;
  camera: THREE.PerspectiveCamera | null;
  renderer: THREE.WebGLRenderer | null;
  composer: EffectComposer | null;
  bloom: UnrealBloomPass | null;
  stars: THREE.Points[];
  nebula: THREE.Mesh | null;
  atmosphere: THREE.Mesh | null;
  animationId: number | null;
};

export function HeroCanvas({ className }: { className?: string }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  // Smoothed pointer target (-0.5..0.5 each axis); the camera eases toward it each frame.
  const pointer = useRef({ x: 0, y: 0 });
  const smooth = useRef({ x: 0, y: 0 });
  const refs = useRef<Refs>({
    scene: null,
    camera: null,
    renderer: null,
    composer: null,
    bloom: null,
    stars: [],
    nebula: null,
    atmosphere: null,
    animationId: null,
  });

  useEffect(() => {
    const container = containerRef.current;
    const canvas = canvasRef.current;
    if (!container || !canvas) return;

    const size = () => ({
      w: container.clientWidth || window.innerWidth,
      h: container.clientHeight || Math.round(window.innerHeight * 0.9),
    });

    let { w, h } = size();
    // Fewer points on small / dense-pixel screens — the bloom does the heavy lifting,
    // so a thinner field still reads full while keeping mobile GPUs comfortable.
    const isSmall = w < 768;
    const starCount = isSmall ? 1600 : 4200;
    const pixelRatio = Math.min(window.devicePixelRatio || 1, isSmall ? 1.5 : 2);

    const r = refs.current;

    // ── scene + camera ──────────────────────────────────────────────────────
    r.scene = new THREE.Scene();
    r.scene.fog = new THREE.FogExp2(0x05050a, 0.00028);

    r.camera = new THREE.PerspectiveCamera(70, w / h, 0.1, 2000);
    r.camera.position.set(0, 18, 120);

    // ── renderer (guarded: no WebGL → bail to the CSS fallback) ──────────────
    try {
      r.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
    } catch {
      return;
    }
    r.renderer.setSize(w, h, false);
    r.renderer.setPixelRatio(pixelRatio);
    r.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    r.renderer.toneMappingExposure = 0.62;

    // ── post: bloom turns bright stars into warm gold halos ──────────────────
    r.composer = new EffectComposer(r.renderer);
    r.composer.setSize(w, h);
    r.composer.addPass(new RenderPass(r.scene, r.camera));
    r.bloom = new UnrealBloomPass(new THREE.Vector2(w, h), 0.6, 0.5, 0.6);
    r.composer.addPass(r.bloom);

    // ── starfield: three depth layers, each rotating at its own slow rate ────
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

        // Monochrome palette: pure grayscale stars — bright whites with a scatter of
        // dimmer silvers, so the field reads as black & white, not tinted.
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
        vertexShader: `
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
            gl_PointSize = size * (300.0 / -mvPosition.z);
            gl_Position = projectionMatrix * mvPosition;
          }
        `,
        fragmentShader: `
          varying vec3 vColor;
          void main() {
            float dist = length(gl_PointCoord - vec2(0.5));
            if (dist > 0.5) discard;
            float opacity = 1.0 - smoothstep(0.0, 0.5, dist);
            gl_FragColor = vec4(vColor, opacity);
          }
        `,
        transparent: true,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
      });

      const stars = new THREE.Points(geometry, material);
      r.scene.add(stars);
      r.stars.push(stars);
    }

    // ── nebula: a slow grayscale wash far behind the field ───────────────────
    {
      const geometry = new THREE.PlaneGeometry(8000, 4000, 80, 80);
      const material = new THREE.ShaderMaterial({
        uniforms: {
          time: { value: 0 },
          color1: { value: new THREE.Color(0xcfcfd4) },
          color2: { value: new THREE.Color(0x161618) },
          opacity: { value: 0.2 },
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

    // ── atmosphere: a faint cool-white rim that pulses, sitting around the camera ──
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

    // ── render ────────────────────────────────────────────────────────────
    // Manual elapsed-time clock — THREE.Clock is deprecated as of three r185.
    const t0 = performance.now();

    const renderFrame = () => {
      const t = (performance.now() - t0) / 1000;
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
        // ease toward the smoothed pointer + a gentle autonomous float
        smooth.current.x += (pointer.current.x - smooth.current.x) * 0.05;
        smooth.current.y += (pointer.current.y - smooth.current.y) * 0.05;
        r.camera.position.x = smooth.current.x * 30 + Math.sin(t * 0.1) * 2;
        r.camera.position.y = 18 - smooth.current.y * 18 + Math.cos(t * 0.15) * 1.5;
        r.camera.lookAt(0, 6, -400);
      }
      r.composer?.render();
    };

    const loop = () => {
      r.animationId = requestAnimationFrame(loop);
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

    // The loop only runs while the hero is BOTH on-screen and in a visible tab — a
    // starfield rendering at 60fps behind the FAQ would drain GPU/battery for nothing.
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
      { threshold: 0.04 },
    );
    const onVisibility = () => evaluate();

    // ── pointer parallax ──────────────────────────────────────────────────────
    const onPointerMove = (e: PointerEvent) => {
      pointer.current.x = (e.clientX / window.innerWidth - 0.5) * 2;
      pointer.current.y = (e.clientY / window.innerHeight - 0.5) * 2;
    };

    // The exact, always-on animation — starfield drift + pointer-tracked camera for every
    // visitor (no reduced-motion downgrade). The IntersectionObserver only pauses the loop
    // while the hero is fully off-screen; the visible animation is never altered.
    renderFrame(); // immediate first paint, then the observer starts the loop
    io.observe(container);
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("pointermove", onPointerMove, { passive: true });

    // ── WebGL context loss: pause cleanly, resume when the GPU comes back ─────
    const onContextLost = (e: Event) => {
      e.preventDefault();
      stopLoop();
    };
    const onContextRestored = () => {
      evaluate();
    };
    canvas.addEventListener("webglcontextlost", onContextLost);
    canvas.addEventListener("webglcontextrestored", onContextRestored);

    // ── responsive: track the container, not the window ─────────────────────
    const onResize = () => {
      const next = size();
      // Ignore degenerate mid-layout measurements (e.g. a 0–8px container snapshot during
      // a reflow) — applying them would freeze the drawing buffer at a garbage resolution
      // until the next change. Hold the last good size until a sane one arrives.
      if (next.w < 50 || next.h < 50) return;
      w = next.w;
      h = next.h;
      if (!r.camera || !r.renderer || !r.composer || !r.bloom) return;
      r.camera.aspect = w / h;
      r.camera.updateProjectionMatrix();
      r.renderer.setSize(w, h, false);
      r.composer.setSize(w, h);
      r.bloom.setSize(w, h);
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
      window.removeEventListener("pointermove", onPointerMove);
      canvas.removeEventListener("webglcontextlost", onContextLost);
      canvas.removeEventListener("webglcontextrestored", onContextRestored);
      for (const s of r.stars) {
        s.geometry.dispose();
        (s.material as THREE.Material).dispose();
      }
      r.stars = [];
      if (r.nebula) {
        r.nebula.geometry.dispose();
        (r.nebula.material as THREE.Material).dispose();
      }
      if (r.atmosphere) {
        r.atmosphere.geometry.dispose();
        (r.atmosphere.material as THREE.Material).dispose();
      }
      // Dispose each pass's render targets before the composer (composer.dispose()
      // alone leaves the bloom pass's targets allocated — a VRAM leak across remounts).
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
  }, []);

  return (
    <div ref={containerRef} className={className} aria-hidden>
      <canvas ref={canvasRef} className="h-full w-full" />
    </div>
  );
}

export default HeroCanvas;
