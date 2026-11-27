// Interactive STL viewer: parts coloured per spec, printer bed grid, orbit controls. Z is up.
import * as THREE from "three";
import { OrbitControls } from "./vendor/OrbitControls.js";
import { STLLoader } from "./vendor/STLLoader.js";

export class Viewer {
  constructor(el) {
    this.el = el;
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(35, 1, 0.1, 5000);
    this.camera.up.set(0, 0, 1);
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    el.appendChild(this.renderer.domElement);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.scene.add(new THREE.HemisphereLight(0xffffff, 0x8a8f99, 1.6));
    const key = new THREE.DirectionalLight(0xffffff, 1.6);
    key.position.set(1, -1.4, 2);
    this.scene.add(key);
    const rim = new THREE.DirectionalLight(0xffffff, 0.5);
    rim.position.set(-1.5, 1, 0.5);
    this.scene.add(rim);
    this.group = new THREE.Group();
    this.scene.add(this.group);
    this.bed = null;
    this.loader = new STLLoader();
    new ResizeObserver(() => this.resize()).observe(el);
    this.resize();
    const tick = () => { this.controls.update(); this.renderer.render(this.scene, this.camera); requestAnimationFrame(tick); };
    tick();
  }

  resize() {
    const w = this.el.clientWidth || 600, h = this.el.clientHeight || 400;
    this.renderer.setSize(w, h);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }

  setBed(size) {
    if (this.bed) this.scene.remove(this.bed);
    const [sx, sy] = size;
    const g = new THREE.Group();
    const grid = new THREE.GridHelper(Math.max(sx, sy), Math.round(Math.max(sx, sy) / 10), 0x9aa0a8, 0xc9cdd2);
    grid.rotation.x = Math.PI / 2;
    grid.material.transparent = true;
    grid.material.opacity = 0.55;
    g.add(grid);
    const outline = new THREE.LineSegments(
      new THREE.EdgesGeometry(new THREE.PlaneGeometry(sx, sy)),
      new THREE.LineBasicMaterial({ color: 0xe8743b }));
    g.add(outline);
    this.bed = g;
    this.scene.add(g);
  }

  async show(parts, { onBed = false } = {}) {
    while (this.group.children.length) {
      const m = this.group.children.pop();
      m.geometry.dispose(); m.material.dispose();
    }
    const box = new THREE.Box3();
    const meshes = await Promise.all(parts.map(async (p) => {
      const geom = await this.loader.loadAsync(p.url);
      geom.computeVertexNormals();
      const mat = new THREE.MeshStandardMaterial({ color: new THREE.Color(p.color || "#cccccc"), roughness: 0.62, metalness: 0.02 });
      const mesh = new THREE.Mesh(geom, mat);
      mesh.name = p.name;
      return mesh;
    }));
    if (onBed && meshes.length > 1) {   // lay print-pose parts side by side like the slicer would
      let x = 0;
      const gap = 6;
      const widths = meshes.map((m) => { m.geometry.computeBoundingBox(); const b = m.geometry.boundingBox; return b.max.x - b.min.x; });
      const total = widths.reduce((a, b) => a + b, 0) + gap * (meshes.length - 1);
      x = -total / 2;
      meshes.forEach((m, i) => { const b = m.geometry.boundingBox; m.position.x = x - b.min.x; x += widths[i] + gap; });
    }
    meshes.forEach((m) => { this.group.add(m); box.expandByObject(m); });
    this.frame(box);
    const s = box.getSize(new THREE.Vector3());
    return [s.x, s.y, s.z];
  }

  frame(box) {
    const c = box.getCenter(new THREE.Vector3());
    const r = Math.max(box.getSize(new THREE.Vector3()).length() / 2, 5);
    const d = r / Math.sin((this.camera.fov * Math.PI) / 360) * 1.1;
    this.camera.position.set(c.x + d * 0.6, c.y - d * 0.75, c.z + d * 0.5);
    this.camera.near = d / 100; this.camera.far = d * 20;
    this.camera.updateProjectionMatrix();
    this.controls.target.copy(c);
    if (this.bed) this.bed.position.set(c.x, c.y, box.min.z - 0.01);
  }
}
