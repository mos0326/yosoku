/* ============================================================
 * 鉄板の極 — ULTRA REALISTIC COOKING 3D
 * Three.js (r128) 製ステーキ調理シミュレータ。外部アセットなし。
 * すべてのテクスチャ・音はプロシージャル生成。
 * ============================================================ */
(function () {
  'use strict';

  // ---------- 定数 ----------
  var TIMESCALE = 6;                 // ゲーム内時間の加速率
  var AMBIENT_C = 20;                // 室温
  var FRIDGE_C = 8;                  // 肉の初期温度
  var ZONES = {
    pan:   { x: -2.2, z: 0.0,  surfaceY: 0.40, r: 1.55, label: 'フライパン' },
    board: { x: 2.8,  z: 0.55, surfaceY: 0.17, r: 1.45, label: 'まな板' },
    plate: { x: 5.55, z: 0.35, surfaceY: 0.16, r: 1.30, label: '皿' }
  };
  var DONENESS = [
    { name: 'レア',           c: 46 },
    { name: 'ミディアムレア', c: 52 },
    { name: 'ミディアム',     c: 57.5 },
    { name: 'ミディアムウェル', c: 63 },
    { name: 'ウェルダン',     c: 69 }
  ];

  function clamp(v, a, b) { return v < a ? a : (v > b ? b : v); }
  function lerp(a, b, t) { return a + (b - a) * t; }
  function rand(a, b) { return a + Math.random() * (b - a); }
  var storage = {
    get: function (k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set: function (k, v) { try { localStorage.setItem(k, v); } catch (e) { /* サンドボックス環境 */ } }
  };

  // ---------- レンダラ / シーン ----------
  var app = document.getElementById('app');
  var renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: 'high-performance' });
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  renderer.outputEncoding = THREE.sRGBEncoding;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.12;
  app.appendChild(renderer.domElement);

  var scene = new THREE.Scene();
  scene.background = new THREE.Color(0x0d0a08);
  scene.fog = new THREE.Fog(0x0d0a08, 18, 34);

  var camera = new THREE.PerspectiveCamera(42, 1, 0.1, 100);

  // 環境マップ(金属の映り込み用の簡易ルームライト)
  (function buildEnv() {
    var envScene = new THREE.Scene();
    var room = new THREE.Mesh(
      new THREE.BoxGeometry(20, 12, 20),
      new THREE.MeshBasicMaterial({ color: 0x2a2119, side: THREE.BackSide })
    );
    envScene.add(room);
    function lightPanel(w, h, color, intensity, pos, rot) {
      var m = new THREE.Mesh(new THREE.PlaneGeometry(w, h),
        new THREE.MeshBasicMaterial({ color: color }));
      m.material.color.multiplyScalar(intensity);
      m.position.copy(pos); m.rotation.set(rot.x, rot.y, rot.z);
      envScene.add(m);
    }
    lightPanel(6, 3, new THREE.Color(0xfff1d8), 6, new THREE.Vector3(0, 5.8, 0), { x: Math.PI / 2, y: 0, z: 0 });
    lightPanel(3, 2, new THREE.Color(0xffd9a8), 3, new THREE.Vector3(-6, 3, -6), { x: 0, y: Math.PI / 4, z: 0 });
    lightPanel(3, 2, new THREE.Color(0xbfd4ff), 1.5, new THREE.Vector3(6, 3, -6), { x: 0, y: -Math.PI / 4, z: 0 });
    var pmrem = new THREE.PMREMGenerator(renderer);
    scene.environment = pmrem.fromScene(envScene, 0.06).texture;
    pmrem.dispose();
  })();

  // ---------- ライティング ----------
  scene.add(new THREE.HemisphereLight(0x8a7a66, 0x14100c, 0.5));
  var key = new THREE.DirectionalLight(0xfff0da, 1.15);
  key.position.set(4.5, 9, 5.5);
  key.castShadow = true;
  key.shadow.mapSize.set(2048, 2048);
  key.shadow.camera.left = -9; key.shadow.camera.right = 9;
  key.shadow.camera.top = 9; key.shadow.camera.bottom = -9;
  key.shadow.camera.near = 1; key.shadow.camera.far = 30;
  key.shadow.bias = -0.0004;
  scene.add(key);
  var stoveSpot = new THREE.SpotLight(0xffe2b8, 0.8, 20, Math.PI / 5, 0.5, 1.2);
  stoveSpot.position.set(-2.2, 7, 1.5);
  stoveSpot.target.position.set(-2.2, 0, 0);
  scene.add(stoveSpot); scene.add(stoveSpot.target);
  var flameLight = new THREE.PointLight(0xff7a22, 0, 6, 2);
  flameLight.position.set(ZONES.pan.x, 0.25, ZONES.pan.z);
  scene.add(flameLight);

  // ---------- プロシージャルテクスチャ ----------
  function canvasTexture(size, draw, repeat) {
    var c = document.createElement('canvas');
    c.width = c.height = size;
    draw(c.getContext('2d'), size);
    var t = new THREE.CanvasTexture(c);
    t.encoding = THREE.sRGBEncoding;
    if (repeat) { t.wrapS = t.wrapT = THREE.RepeatWrapping; t.repeat.set(repeat, repeat); }
    return t;
  }

  var woodTex = canvasTexture(512, function (g, s) {
    g.fillStyle = '#8a5a33'; g.fillRect(0, 0, s, s);
    for (var i = 0; i < 90; i++) {
      g.strokeStyle = 'rgba(' + (70 + Math.random() * 60 | 0) + ',' + (40 + Math.random() * 35 | 0) + ',20,' + rand(0.08, 0.3) + ')';
      g.lineWidth = rand(1, 5);
      g.beginPath();
      var y = Math.random() * s;
      g.moveTo(0, y);
      for (var x = 0; x <= s; x += 32) g.lineTo(x, y + Math.sin(x * 0.02 + i) * 4 + rand(-2, 2));
      g.stroke();
    }
    for (i = 0; i < 6; i++) { // 節
      var kx = Math.random() * s, ky = Math.random() * s;
      var gr = g.createRadialGradient(kx, ky, 1, kx, ky, rand(8, 18));
      gr.addColorStop(0, 'rgba(50,28,12,0.8)'); gr.addColorStop(1, 'rgba(50,28,12,0)');
      g.fillStyle = gr; g.beginPath(); g.arc(kx, ky, 20, 0, 7); g.fill();
    }
  }, 2);

  var counterTex = canvasTexture(512, function (g, s) {
    g.fillStyle = '#20242a'; g.fillRect(0, 0, s, s);
    for (var i = 0; i < 5000; i++) {
      g.fillStyle = 'rgba(' + (140 + Math.random() * 100 | 0) + ',' + (145 + Math.random() * 90 | 0) + ',' + (150 + Math.random() * 90 | 0) + ',' + rand(0.02, 0.1) + ')';
      g.fillRect(Math.random() * s, Math.random() * s, rand(1, 2.5), rand(1, 2.5));
    }
  }, 3);

  var tileTex = canvasTexture(512, function (g, s) {
    g.fillStyle = '#26282a'; g.fillRect(0, 0, s, s);
    var n = 4, t = s / n;
    for (var i = 0; i < n; i++) for (var j = 0; j < n; j++) {
      var v = 52 + Math.random() * 10 | 0;
      g.fillStyle = 'rgba(' + v + ',' + (v + 2) + ',' + (v + 5) + ',1)';
      g.fillRect(i * t + 3, j * t + 3, t - 6, t - 6);
      var gr = g.createLinearGradient(i * t, j * t, i * t, j * t + t);
      gr.addColorStop(0, 'rgba(255,240,220,0.10)'); gr.addColorStop(0.5, 'rgba(0,0,0,0)');
      g.fillStyle = gr; g.fillRect(i * t + 3, j * t + 3, t - 6, t - 6);
    }
  }, 4);

  function softSpriteTex(inner, outer) {
    var c = document.createElement('canvas'); c.width = c.height = 64;
    var g = c.getContext('2d');
    var gr = g.createRadialGradient(32, 32, 2, 32, 32, 30);
    gr.addColorStop(0, inner); gr.addColorStop(1, outer);
    g.fillStyle = gr; g.fillRect(0, 0, 64, 64);
    return new THREE.CanvasTexture(c);
  }
  var puffTex = softSpriteTex('rgba(255,255,255,0.9)', 'rgba(255,255,255,0)');
  var flameTexOrange = (function () {
    var c = document.createElement('canvas'); c.width = 64; c.height = 128;
    var g = c.getContext('2d');
    var gr = g.createRadialGradient(32, 96, 4, 32, 80, 60);
    gr.addColorStop(0, 'rgba(255,230,150,0.95)');
    gr.addColorStop(0.35, 'rgba(255,140,40,0.65)');
    gr.addColorStop(0.75, 'rgba(220,60,20,0.18)');
    gr.addColorStop(1, 'rgba(200,40,10,0)');
    g.fillStyle = gr; g.fillRect(0, 0, 64, 128);
    return new THREE.CanvasTexture(c);
  })();
  var flameTexBlue = (function () {
    var c = document.createElement('canvas'); c.width = 64; c.height = 64;
    var g = c.getContext('2d');
    var gr = g.createRadialGradient(32, 40, 2, 32, 36, 28);
    gr.addColorStop(0, 'rgba(160,210,255,0.9)');
    gr.addColorStop(0.6, 'rgba(60,110,255,0.5)');
    gr.addColorStop(1, 'rgba(30,60,220,0)');
    g.fillStyle = gr; g.fillRect(0, 0, 64, 64);
    return new THREE.CanvasTexture(c);
  })();

  // ---------- キッチンのモデル ----------
  var kitchen = new THREE.Group();
  scene.add(kitchen);

  function addMesh(geo, mat, x, y, z, opts) {
    var m = new THREE.Mesh(geo, mat);
    m.position.set(x, y, z);
    m.castShadow = !(opts && opts.noCast);
    m.receiveShadow = !(opts && opts.noReceive);
    kitchen.add(m);
    return m;
  }

  // カウンター天板 / 壁 / 床
  addMesh(new THREE.BoxGeometry(17, 0.5, 9.4),
    new THREE.MeshStandardMaterial({ map: counterTex, roughness: 0.55, metalness: 0.08 }),
    0, -0.25, 0, { noCast: true });
  addMesh(new THREE.BoxGeometry(17, 1.1, 9.4),
    new THREE.MeshStandardMaterial({ color: 0x241c14, roughness: 0.8 }),
    0, -1.05, 0, { noCast: true });
  var wall = addMesh(new THREE.PlaneGeometry(17, 7),
    new THREE.MeshStandardMaterial({ map: tileTex, roughness: 0.5, metalness: 0.05 }),
    0, 3.0, -4.68, { noCast: true });
  addMesh(new THREE.PlaneGeometry(40, 26),
    new THREE.MeshStandardMaterial({ color: 0x17120d, roughness: 0.95 }),
    0, -1.6, 6, { noCast: true }).rotation.x = -Math.PI / 2;

  // ガスコンロ
  var hob = addMesh(new THREE.BoxGeometry(4.9, 0.09, 4.4),
    new THREE.MeshStandardMaterial({ color: 0x0c0c0e, roughness: 0.25, metalness: 0.55 }),
    ZONES.pan.x, 0.045, ZONES.pan.z);
  var grateMat = new THREE.MeshStandardMaterial({ color: 0x15151a, roughness: 0.55, metalness: 0.6 });
  addMesh(new THREE.TorusGeometry(1.3, 0.06, 10, 40), grateMat, ZONES.pan.x, 0.2, ZONES.pan.z)
    .rotation.x = Math.PI / 2;
  for (var gi = 0; gi < 5; gi++) {
    var fin = addMesh(new THREE.BoxGeometry(0.95, 0.07, 0.12), grateMat, ZONES.pan.x, 0.24, ZONES.pan.z);
    fin.position.x += Math.cos(gi / 5 * Math.PI * 2) * 0.95;
    fin.position.z += Math.sin(gi / 5 * Math.PI * 2) * 0.95;
    fin.rotation.y = -gi / 5 * Math.PI * 2 + Math.PI / 2;
  }
  addMesh(new THREE.CylinderGeometry(0.34, 0.38, 0.1, 24),
    new THREE.MeshStandardMaterial({ color: 0x333338, roughness: 0.35, metalness: 0.8 }),
    ZONES.pan.x, 0.1, ZONES.pan.z);
  addMesh(new THREE.CylinderGeometry(0.13, 0.15, 0.16, 16),
    new THREE.MeshStandardMaterial({ color: 0x44444a, roughness: 0.3, metalness: 0.7 }),
    ZONES.pan.x + 1.85, 0.12, ZONES.pan.z + 2.0);
  // 火の照り返し(パンの縁の下からのぞくオレンジの輪)
  var glowRing = addMesh(new THREE.RingGeometry(1.35, 1.85, 48),
    new THREE.MeshBasicMaterial({
      color: 0xff6a18, transparent: true, opacity: 0,
      blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.DoubleSide
    }),
    ZONES.pan.x, 0.16, ZONES.pan.z, { noCast: true, noReceive: true });
  glowRing.rotation.x = -Math.PI / 2;

  // 炎(スプライト)
  var flames = [];
  for (var fi = 0; fi < 12; fi++) {
    var isBlue = fi >= 8;
    var sp = new THREE.Sprite(new THREE.SpriteMaterial({
      map: isBlue ? flameTexBlue : flameTexOrange,
      blending: THREE.AdditiveBlending, depthWrite: false, transparent: true, opacity: 0
    }));
    var ang = (fi % 8) / 8 * Math.PI * 2;
    var rr = isBlue ? 0.42 : 0.62;
    sp.position.set(ZONES.pan.x + Math.cos(ang) * rr, 0.24, ZONES.pan.z + Math.sin(ang) * rr);
    sp.scale.set(0.001, 0.001, 1);
    scene.add(sp);
    flames.push({ s: sp, blue: isBlue, phase: Math.random() * 10 });
  }

  // フライパン
  var panGroup = new THREE.Group();
  panGroup.position.set(ZONES.pan.x, 0.28, ZONES.pan.z);
  scene.add(panGroup);
  var panProfile = [
    new THREE.Vector2(0.0, 0.0), new THREE.Vector2(1.55, 0.0),
    new THREE.Vector2(1.78, 0.30), new THREE.Vector2(1.84, 0.46),
    new THREE.Vector2(1.72, 0.47), new THREE.Vector2(1.62, 0.30),
    new THREE.Vector2(1.42, 0.075), new THREE.Vector2(0.0, 0.07)
  ];
  var panMesh = new THREE.Mesh(new THREE.LatheGeometry(panProfile, 48),
    new THREE.MeshStandardMaterial({
      color: 0x191919, roughness: 0.38, metalness: 0.85, side: THREE.DoubleSide,
      emissive: 0xff2a00, emissiveIntensity: 0
    }));
  panMesh.castShadow = panMesh.receiveShadow = true;
  panGroup.add(panMesh);
  var handle = new THREE.Mesh(new THREE.BoxGeometry(1.7, 0.13, 0.26),
    new THREE.MeshStandardMaterial({ color: 0x101010, roughness: 0.7, metalness: 0.2 }));
  handle.position.set(2.6, 0.5, 0); handle.rotation.z = 0.12;
  handle.castShadow = true;
  panGroup.add(handle);
  panGroup.rotation.y = -0.6;
  var oilMesh = new THREE.Mesh(new THREE.CircleGeometry(1.32, 40),
    new THREE.MeshStandardMaterial({
      color: 0x2e2306, roughness: 0.06, metalness: 0.0,
      transparent: true, opacity: 0.0, depthWrite: false
    }));
  oilMesh.rotation.x = -Math.PI / 2;
  oilMesh.position.y = 0.085;
  panGroup.add(oilMesh);
  var butterMesh = new THREE.Mesh(new THREE.BoxGeometry(0.3, 0.2, 0.3),
    new THREE.MeshStandardMaterial({ color: 0xf5d878, roughness: 0.35 }));
  butterMesh.position.set(0.7, 0.2, 0.4);
  butterMesh.visible = false;
  panGroup.add(butterMesh);

  // まな板
  addMesh(new THREE.BoxGeometry(2.9, 0.17, 2.0),
    new THREE.MeshStandardMaterial({ map: woodTex, roughness: 0.7 }),
    ZONES.board.x, 0.085, ZONES.board.z);

  // 皿
  var plateProfile = [
    new THREE.Vector2(0, 0.02), new THREE.Vector2(0.85, 0.03),
    new THREE.Vector2(1.1, 0.09), new THREE.Vector2(1.32, 0.16),
    new THREE.Vector2(1.38, 0.155)
  ];
  var plate = addMesh(new THREE.LatheGeometry(plateProfile, 48),
    new THREE.MeshStandardMaterial({ color: 0xf2ede4, roughness: 0.22, metalness: 0.02, side: THREE.DoubleSide }),
    ZONES.plate.x, 0.01, ZONES.plate.z);

  // 付け合わせ(盛り付け時に表示)
  var garnish = new THREE.Group();
  garnish.position.set(ZONES.plate.x, ZONES.plate.surfaceY, ZONES.plate.z);
  var beanMat = new THREE.MeshStandardMaterial({ color: 0x3f7a2f, roughness: 0.5 });
  for (var bi = 0; bi < 3; bi++) {
    var bean = new THREE.Mesh(new THREE.CylinderGeometry(0.05, 0.045, rand(0.85, 1.0), 8), beanMat);
    bean.rotation.z = Math.PI / 2; bean.rotation.y = rand(-0.3, 0.3);
    bean.position.set(-0.15 + bi * 0.02, 0.05 + bi * 0.06, -0.62 - bi * 0.05);
    bean.castShadow = true;
    garnish.add(bean);
  }
  var tomato = new THREE.Mesh(new THREE.SphereGeometry(0.17, 20, 16),
    new THREE.MeshStandardMaterial({ color: 0xd23b28, roughness: 0.15 }));
  tomato.position.set(0.75, 0.14, -0.55); tomato.castShadow = true;
  garnish.add(tomato);
  garnish.visible = false;
  scene.add(garnish);

  // 置き場のハイライトリング(ドラッグ中に表示)
  var ringMat = new THREE.MeshBasicMaterial({ color: 0xe8b34b, transparent: true, opacity: 0, side: THREE.DoubleSide });
  var rings = {};
  Object.keys(ZONES).forEach(function (k) {
    var z = ZONES[k];
    var ring = new THREE.Mesh(new THREE.RingGeometry(z.r * 0.82, z.r * 0.95, 48), ringMat.clone());
    ring.rotation.x = -Math.PI / 2;
    ring.position.set(z.x, z.surfaceY + 0.06, z.z);
    scene.add(ring);
    rings[k] = ring;
  });

  // ---------- ステーキ ----------
  function makeSteakGeometry(w, d, h) {
    var pts = [];
    var N = 48;
    for (var i = 0; i < N; i++) {
      var a = i / N * Math.PI * 2;
      var co = Math.cos(a), si = Math.sin(a);
      var r = 1 / Math.pow(Math.pow(Math.abs(co / (w / 2)), 3.2) + Math.pow(Math.abs(si / (d / 2)), 3.2), 1 / 3.2);
      var wob = 1 + 0.05 * Math.sin(a * 3 + 1.7) + 0.035 * Math.sin(a * 5 + 0.4);
      pts.push(new THREE.Vector2(co * r * wob, si * r * wob));
    }
    var shape = new THREE.Shape(pts);
    var geo = new THREE.ExtrudeGeometry(shape, {
      depth: h * 0.6, bevelEnabled: true, bevelThickness: h * 0.2,
      bevelSize: 0.07, bevelSegments: 3, steps: 1
    });
    geo.center();
    geo.rotateX(-Math.PI / 2);

    // 三角形を法線方向で 上面/下面/側面 に分類し、マテリアルグループを再構築
    var pos = geo.attributes.position;
    var triCount = pos.count / 3;
    var buckets = [[], [], []];
    var A = new THREE.Vector3(), B = new THREE.Vector3(), C = new THREE.Vector3();
    var AB = new THREE.Vector3(), AC = new THREE.Vector3(), Nv = new THREE.Vector3();
    for (var t = 0; t < triCount; t++) {
      A.fromBufferAttribute(pos, t * 3); B.fromBufferAttribute(pos, t * 3 + 1); C.fromBufferAttribute(pos, t * 3 + 2);
      AB.subVectors(B, A); AC.subVectors(C, A); Nv.crossVectors(AB, AC).normalize();
      buckets[Nv.y > 0.4 ? 0 : (Nv.y < -0.4 ? 1 : 2)].push(t);
    }
    var newPos = [], newUv = [];
    var bb = new THREE.Box3().setFromBufferAttribute(pos);
    var size = new THREE.Vector3(); bb.getSize(size);
    var groups = [];
    var offset = 0;
    for (var bkt = 0; bkt < 3; bkt++) {
      var list = buckets[bkt];
      for (var li = 0; li < list.length; li++) {
        var tt = list[li];
        for (var vi = 0; vi < 3; vi++) {
          var idx = tt * 3 + vi;
          var px = pos.getX(idx), py = pos.getY(idx), pz = pos.getZ(idx);
          newPos.push(px, py, pz);
          if (bkt === 2) {
            newUv.push(Math.atan2(pz, px) / (Math.PI * 2) + 0.5, (py - bb.min.y) / size.y);
          } else {
            newUv.push((px - bb.min.x) / size.x, (pz - bb.min.z) / size.z);
          }
        }
      }
      groups.push({ start: offset, count: list.length * 3, mat: bkt });
      offset += list.length * 3;
    }
    var out = new THREE.BufferGeometry();
    out.setAttribute('position', new THREE.Float32BufferAttribute(newPos, 3));
    out.setAttribute('uv', new THREE.Float32BufferAttribute(newUv, 2));
    groups.forEach(function (gr) { out.addGroup(gr.start, gr.count, gr.mat); });
    out.computeVertexNormals();
    out.computeBoundingBox();
    return out;
  }

  // 面ごとの焼き色ペインタ(生レイヤ+クラストレイヤ+焦げレイヤの合成)
  function makeFacePainter(size) {
    function layer(drawFn) {
      var c = document.createElement('canvas'); c.width = c.height = size;
      drawFn(c.getContext('2d'), size);
      return c;
    }
    var raw = layer(function (g, s) {
      var gr = g.createRadialGradient(s / 2, s / 2, s * 0.1, s / 2, s / 2, s * 0.62);
      gr.addColorStop(0, '#b02330'); gr.addColorStop(0.75, '#9c1e29'); gr.addColorStop(1, '#7e1820');
      g.fillStyle = gr; g.fillRect(0, 0, s, s);
      g.lineCap = 'round';
      for (var i = 0; i < 26; i++) {  // サシ(脂の筋)
        g.strokeStyle = 'rgba(255,238,232,' + rand(0.18, 0.5) + ')';
        g.lineWidth = rand(0.8, 2.8);
        g.beginPath();
        var x = rand(0, s), y = rand(0, s);
        g.moveTo(x, y);
        for (var k = 0; k < 4; k++) { x += rand(-s / 5, s / 5); y += rand(-s / 7, s / 7); g.lineTo(x, y); }
        g.stroke();
      }
      g.fillStyle = 'rgba(244,228,196,0.85)';   // 脂身の縁
      g.beginPath(); g.ellipse(s / 2, -s * 0.32, s * 0.62, s * 0.45, 0, 0, 7); g.fill();
    });
    var grey = layer(function (g, s) {  // 火が入り始めの退色
      g.fillStyle = '#9a7a68'; g.fillRect(0, 0, s, s);
      for (var i = 0; i < 300; i++) {
        g.fillStyle = 'rgba(' + (150 + Math.random() * 40 | 0) + ',' + (110 + Math.random() * 30 | 0) + ',' + (85 + Math.random() * 25 | 0) + ',0.25)';
        g.beginPath(); g.arc(rand(0, s), rand(0, s), rand(2, 9), 0, 7); g.fill();
      }
    });
    var crust = layer(function (g, s) { // メイラードのクラスト
      g.fillStyle = '#7d4517'; g.fillRect(0, 0, s, s);
      for (var i = 0; i < 240; i++) {
        var x = rand(0, s), y = rand(0, s), r = rand(3, 16);
        var gr = g.createRadialGradient(x, y, 0.5, x, y, r);
        var dark = Math.random() < 0.5;
        gr.addColorStop(0, dark ? 'rgba(52,26,8,0.85)' : 'rgba(168,96,40,0.8)');
        gr.addColorStop(1, 'rgba(0,0,0,0)');
        g.fillStyle = gr; g.beginPath(); g.arc(x, y, r, 0, 7); g.fill();
      }
      for (i = 0; i < 700; i++) {
        g.fillStyle = 'rgba(30,14,4,' + rand(0.1, 0.4) + ')';
        g.fillRect(rand(0, s), rand(0, s), rand(0.6, 2), rand(0.6, 2));
      }
    });
    var burnt = layer(function (g, s) {
      g.fillStyle = '#120a05'; g.fillRect(0, 0, s, s);
      for (var i = 0; i < 160; i++) {
        var x = rand(0, s), y = rand(0, s), r = rand(4, 22);
        var gr = g.createRadialGradient(x, y, 0.5, x, y, r);
        gr.addColorStop(0, 'rgba(0,0,0,0.9)'); gr.addColorStop(1, 'rgba(0,0,0,0)');
        g.fillStyle = gr; g.beginPath(); g.arc(x, y, r, 0, 7); g.fill();
      }
    });
    var out = document.createElement('canvas'); out.width = out.height = size;
    var g = out.getContext('2d');
    var tex = new THREE.CanvasTexture(out);
    tex.encoding = THREE.sRGBEncoding;
    var lastDrawn = -1;
    function update(cook) {
      if (Math.abs(cook - lastDrawn) < 0.02 && lastDrawn >= 0) return;
      lastDrawn = cook;
      g.globalAlpha = 1; g.drawImage(raw, 0, 0);
      g.globalAlpha = clamp(cook / 0.5, 0, 1) * 0.62; g.drawImage(grey, 0, 0);
      g.globalAlpha = clamp((cook - 0.18) / 0.8, 0, 1); g.drawImage(crust, 0, 0);
      g.globalAlpha = clamp((cook - 1.08) / 0.5, 0, 0.96); g.drawImage(burnt, 0, 0);
      g.globalAlpha = 1;
      tex.needsUpdate = true;
    }
    update(0);
    return { texture: tex, update: update };
  }

  function makeSidePainter(size) {
    var c = document.createElement('canvas'); c.width = c.height = size;
    var g = c.getContext('2d');
    var tex = new THREE.CanvasTexture(c);
    tex.encoding = THREE.sRGBEncoding;
    tex.wrapS = THREE.RepeatWrapping;
    var lastDrawn = -1;
    function base() {
      g.fillStyle = '#a8323c'; g.fillRect(0, 0, size, size);
      for (var i = 0; i < 40; i++) {
        g.fillStyle = 'rgba(210,150,150,' + rand(0.08, 0.25) + ')';
        g.fillRect(rand(0, size), rand(0, size), rand(4, 20), rand(2, 5));
      }
      g.fillStyle = '#efe0c2';  // 上部の脂身の帯
      g.fillRect(0, size * 0.78, size, size * 0.22);
      for (i = 0; i < 60; i++) {
        g.fillStyle = 'rgba(190,160,120,' + rand(0.1, 0.3) + ')';
        g.fillRect(rand(0, size), size * rand(0.78, 1), rand(2, 9), rand(1, 3));
      }
    }
    function update(cook) {
      if (Math.abs(cook - lastDrawn) < 0.03 && lastDrawn >= 0) return;
      lastDrawn = cook;
      base();
      var a = clamp(cook, 0, 1);
      var gr = g.createLinearGradient(0, size, 0, 0);
      gr.addColorStop(0, 'rgba(74,40,14,' + (a * 0.95) + ')');
      gr.addColorStop(0.6, 'rgba(110,62,26,' + (a * 0.65) + ')');
      gr.addColorStop(1, 'rgba(120,70,30,' + (a * 0.3) + ')');
      g.fillStyle = gr; g.fillRect(0, 0, size, size);
      if (cook > 1.05) {
        g.fillStyle = 'rgba(10,6,3,' + clamp((cook - 1.05) / 0.5, 0, 0.9) + ')';
        g.fillRect(0, 0, size, size);
      }
      tex.needsUpdate = true;
    }
    update(0);
    return { texture: tex, update: update };
  }

  var STEAK_H = 0.46;
  var steakGeo = makeSteakGeometry(2.0, 1.45, STEAK_H);
  var steakHalfH = (steakGeo.boundingBox.max.y - steakGeo.boundingBox.min.y) / 2;

  var steak = null;   // 現在のステーキ状態
  var steakMesh = null;

  function newSteak() {
    if (steakMesh) { scene.remove(steakMesh); }
    var faceA = makeFacePainter(512), faceB = makeFacePainter(512), side = makeSidePainter(256);
    function meat(map) {
      return new THREE.MeshStandardMaterial({ map: map, roughness: 0.62, metalness: 0.0 });
    }
    var matTop = meat(faceA.texture), matBottom = meat(faceB.texture), matSide = meat(side.texture);
    steakMesh = new THREE.Mesh(steakGeo, [matTop, matBottom, matSide]);
    steakMesh.castShadow = steakMesh.receiveShadow = true;
    scene.add(steakMesh);

    // 塩・こしょうの粒
    function specks(color, size) {
      var n = 130, arr = new Float32Array(n * 3);
      for (var i = 0; i < n; i++) {
        var a = Math.random() * Math.PI * 2, rr = Math.sqrt(Math.random());
        arr[i * 3] = Math.cos(a) * rr * 0.85;
        arr[i * 3 + 1] = steakHalfH + 0.012;
        arr[i * 3 + 2] = Math.sin(a) * rr * 0.6;
      }
      var gg = new THREE.BufferGeometry();
      gg.setAttribute('position', new THREE.BufferAttribute(arr, 3));
      var pm = new THREE.PointsMaterial({ color: color, size: size, sizeAttenuation: false, depthWrite: false });
      var p = new THREE.Points(gg, pm);
      p.visible = false;
      steakMesh.add(p);
      return p;
    }
    steak = {
      sides: [
        { cook: 0, painter: faceA },
        { cook: 0, painter: faceB }
      ],
      sidePainter: side,
      up: 0,                 // 上を向いている面 index
      core: FRIDGE_C,
      moisture: 1,
      location: 'board',     // board | pan | plate | drag
      resting: 0,            // まな板で休ませた時間(sim秒)
      carry: 0,              // 余熱で入る温度の残り
      salted: false, peppered: false, saltTiming: 0,
      oilUsedAtCook: false, buttered: false,
      flips: 0, servedRaw: false,
      saltDots: specks(0xffffff, 2.2),
      pepperDots: specks(0x2a2018, 2.6)
    };
    placeSteak('board');
    applyFaceMaterials();
    return steak;
  }

  function applyFaceMaterials() {
    var up = steak.sides[steak.up], down = steak.sides[1 - steak.up];
    steakMesh.material[0].map = up.painter.texture;
    steakMesh.material[1].map = down.painter.texture;
    steakMesh.material[0].needsUpdate = true;
    steakMesh.material[1].needsUpdate = true;
  }

  function placeSteak(zoneKey) {
    var z = ZONES[zoneKey];
    steak.location = zoneKey;
    steakMesh.position.set(z.x + (zoneKey === 'plate' ? 0.1 : 0), z.surfaceY + steakHalfH, z.z + (zoneKey === 'plate' ? 0.15 : 0));
    steakMesh.rotation.set(0, zoneKey === 'plate' ? 0.5 : (zoneKey === 'pan' ? 0.25 : -0.15), 0);
    if (zoneKey === 'plate') garnish.visible = true;
  }

  // ---------- パーティクル ----------
  function ParticleSystem(count, texture, blending) {
    this.count = count;
    this.pos = new Float32Array(count * 3);
    this.vel = [];
    this.life = new Float32Array(count);
    this.maxLife = new Float32Array(count);
    this.grow = new Float32Array(count);
    this.sizes = new Float32Array(count);
    this.alphas = new Float32Array(count);
    this.colors = new Float32Array(count * 3);
    this.alphaMax = new Float32Array(count);
    this.gravity = new Float32Array(count);
    for (var i = 0; i < count; i++) { this.vel.push(new THREE.Vector3()); this.life[i] = -1; this.pos[i * 3 + 1] = -999; }
    this.geo = new THREE.BufferGeometry();
    this.geo.setAttribute('position', new THREE.BufferAttribute(this.pos, 3));
    this.geo.setAttribute('aSize', new THREE.BufferAttribute(this.sizes, 1));
    this.geo.setAttribute('aAlpha', new THREE.BufferAttribute(this.alphas, 1));
    this.geo.setAttribute('aColor', new THREE.BufferAttribute(this.colors, 3));
    this.mat = new THREE.ShaderMaterial({
      uniforms: { map: { value: texture }, uPx: { value: 1 } },
      vertexShader: [
        'attribute float aSize; attribute float aAlpha; attribute vec3 aColor;',
        'varying float vA; varying vec3 vC; uniform float uPx;',
        'void main(){',
        '  vA=aAlpha; vC=aColor;',
        '  vec4 mv = modelViewMatrix * vec4(position,1.0);',
        '  gl_PointSize = aSize * uPx * (160.0 / -mv.z);',
        '  gl_Position = projectionMatrix * mv;',
        '}'
      ].join('\n'),
      fragmentShader: [
        'uniform sampler2D map; varying float vA; varying vec3 vC;',
        'void main(){',
        '  vec4 t = texture2D(map, gl_PointCoord);',
        '  gl_FragColor = vec4(vC, vA * t.a);',
        '  if (gl_FragColor.a < 0.003) discard;',
        '}'
      ].join('\n'),
      transparent: true, depthWrite: false, blending: blending || THREE.NormalBlending
    });
    this.points = new THREE.Points(this.geo, this.mat);
    this.points.frustumCulled = false;
    this.cursor = 0;
    scene.add(this.points);
  }
  ParticleSystem.prototype.spawn = function (o) {
    var i = this.cursor; this.cursor = (this.cursor + 1) % this.count;
    this.pos[i * 3] = o.x; this.pos[i * 3 + 1] = o.y; this.pos[i * 3 + 2] = o.z;
    this.vel[i].set(o.vx || 0, o.vy || 0, o.vz || 0);
    this.life[i] = 0; this.maxLife[i] = o.life;
    this.sizes[i] = o.size; this.grow[i] = o.grow || 0;
    this.alphaMax[i] = o.alpha;
    this.gravity[i] = o.gravity || 0;
    this.colors[i * 3] = o.r; this.colors[i * 3 + 1] = o.g; this.colors[i * 3 + 2] = o.b;
  };
  ParticleSystem.prototype.update = function (dt, wiggle) {
    for (var i = 0; i < this.count; i++) {
      if (this.life[i] < 0) continue;
      this.life[i] += dt;
      var t = this.life[i] / this.maxLife[i];
      if (t >= 1) { this.life[i] = -1; this.alphas[i] = 0; this.pos[i * 3 + 1] = -999; continue; }
      this.vel[i].y += this.gravity[i] * dt;
      this.pos[i * 3] += this.vel[i].x * dt + (wiggle ? Math.sin(this.life[i] * 3 + i) * 0.12 * dt : 0);
      this.pos[i * 3 + 1] += this.vel[i].y * dt;
      this.pos[i * 3 + 2] += this.vel[i].z * dt + (wiggle ? Math.cos(this.life[i] * 2.6 + i * 1.7) * 0.12 * dt : 0);
      this.sizes[i] += this.grow[i] * dt;
      this.alphas[i] = this.alphaMax[i] * Math.sin(Math.PI * Math.min(t, 1));
    }
    this.geo.attributes.position.needsUpdate = true;
    this.geo.attributes.aSize.needsUpdate = true;
    this.geo.attributes.aAlpha.needsUpdate = true;
    this.geo.attributes.aColor.needsUpdate = true;
  };

  var smoke = new ParticleSystem(320, puffTex);
  var steam = new ParticleSystem(200, puffTex);
  var splatter = new ParticleSystem(140, puffTex, THREE.AdditiveBlending);

  // ---------- サウンド(WebAudio 合成) ----------
  var AudioFX = {
    ctx: null, master: null, sizzleGain: null, enabled: true, started: false,
    init: function () {
      if (this.started) return;
      var AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return;
      this.ctx = new AC();
      this.master = this.ctx.createGain();
      this.master.gain.value = 0.7;
      this.master.connect(this.ctx.destination);
      var len = this.ctx.sampleRate * 2;
      var buf = this.ctx.createBuffer(1, len, this.ctx.sampleRate);
      var d = buf.getChannelData(0);
      for (var i = 0; i < len; i++) d[i] = Math.random() * 2 - 1;
      this.noiseBuf = buf;
      var src = this.ctx.createBufferSource();
      src.buffer = buf; src.loop = true;
      var bp = this.ctx.createBiquadFilter();
      bp.type = 'bandpass'; bp.frequency.value = 4200; bp.Q.value = 0.7;
      this.sizzleGain = this.ctx.createGain();
      this.sizzleGain.gain.value = 0;
      src.connect(bp); bp.connect(this.sizzleGain); this.sizzleGain.connect(this.master);
      src.start();
      this.started = true;
    },
    resume: function () { this.init(); if (this.ctx && this.ctx.state === 'suspended') this.ctx.resume(); },
    setSizzle: function (v) {
      if (!this.sizzleGain) return;
      var target = this.enabled ? clamp(v, 0, 1) * 0.32 : 0;
      this.sizzleGain.gain.setTargetAtTime(target, this.ctx.currentTime, 0.15);
    },
    burst: function (freq, dur, vol) {
      if (!this.ctx || !this.enabled) return;
      var src = this.ctx.createBufferSource(); src.buffer = this.noiseBuf;
      var bp = this.ctx.createBiquadFilter(); bp.type = 'bandpass'; bp.frequency.value = freq; bp.Q.value = 1.4;
      var gn = this.ctx.createGain();
      var t = this.ctx.currentTime;
      gn.gain.setValueAtTime(vol, t);
      gn.gain.exponentialRampToValueAtTime(0.001, t + dur);
      src.connect(bp); bp.connect(gn); gn.connect(this.master);
      src.start(t, Math.random() * 1.5); src.stop(t + dur + 0.05);
    },
    tone: function (freq, dur, vol, type) {
      if (!this.ctx || !this.enabled) return;
      var o = this.ctx.createOscillator(); o.type = type || 'sine'; o.frequency.value = freq;
      var gn = this.ctx.createGain();
      var t = this.ctx.currentTime;
      gn.gain.setValueAtTime(vol, t);
      gn.gain.exponentialRampToValueAtTime(0.001, t + dur);
      o.connect(gn); gn.connect(this.master);
      o.start(t); o.stop(t + dur + 0.05);
    },
    flip: function () { this.burst(900, 0.12, 0.5); this.tone(95, 0.14, 0.4); },
    ding: function () { this.tone(1046, 0.7, 0.25); this.tone(1568, 0.9, 0.18); },
    click: function () { this.tone(660, 0.05, 0.12, 'triangle'); }
  };

  // ---------- カメラ操作(簡易オービット) ----------
  var camCtl = {
    target: new THREE.Vector3(1.2, 0.35, -0.2),
    theta: -0.1, phi: 1.0, radius: 10.8,
    thetaT: -0.1, phiT: 1.0, radiusT: 10.8,
    apply: function () {
      this.theta = lerp(this.theta, this.thetaT, 0.12);
      this.phi = lerp(this.phi, this.phiT, 0.12);
      this.radius = lerp(this.radius, this.radiusT, 0.12);
      var sp = Math.sin(this.phi), cp = Math.cos(this.phi);
      camera.position.set(
        this.target.x + this.radius * sp * Math.sin(this.theta),
        this.target.y + this.radius * cp,
        this.target.z + this.radius * sp * Math.cos(this.theta)
      );
      camera.lookAt(this.target);
    }
  };

  // ---------- ゲーム状態 ----------
  var game = {
    heat: 0, panTemp: AMBIENT_C,
    oiled: false, oilQuality: 1, butter: 0,
    money: parseInt(storage.get('kiwami_money') || '0', 10) || 0,
    order: null, orderCount: 0,
    phase: 'play', sizzle: 0
  };

  function newOrder() {
    var d = DONENESS[Math.floor(Math.random() * DONENESS.length)];
    game.order = d;
    game.orderCount++;
    document.getElementById('orderTarget').textContent = d.name;
    var notes = [
      '油をひいて強火で焼き目、返して中心温度を合わせ、休ませてから提供。',
      '表面はカリッと、中は注文どおりに。焦がすと減点。',
      '塩こしょうは焼く前が基本。休ませると肉汁が落ち着く。',
      'パンをしっかり予熱してから肉を置くと良い焼き目がつく。'
    ];
    document.getElementById('orderNote').textContent = notes[Math.floor(Math.random() * notes.length)];
    var zone = document.getElementById('coreZone');
    var lo = (d.c - 3) / 90 * 100, hi = (d.c + 3) / 90 * 100;
    zone.style.left = lo + '%'; zone.style.width = (hi - lo) + '%';
  }

  // ---------- UI 参照 ----------
  var el = {
    heat: document.getElementById('heat'), heatVal: document.getElementById('heatVal'),
    panTempVal: document.getElementById('panTempVal'), panBar: document.querySelector('#panBar>i'),
    coreTempVal: document.getElementById('coreTempVal'), coreBar: document.querySelector('#coreBar>i'),
    searA: document.querySelector('#searABar>i'), searB: document.querySelector('#searBBar>i'),
    restWrap: document.getElementById('restWrap'), restBar: document.querySelector('#restBar>i'),
    restVal: document.getElementById('restVal'),
    hint: document.getElementById('hint'), toast: document.getElementById('toast'),
    money: document.getElementById('money'),
    btnOil: document.getElementById('btnOil'), btnButter: document.getElementById('btnButter'),
    btnSalt: document.getElementById('btnSalt'), btnPepper: document.getElementById('btnPepper'),
    btnFlip: document.getElementById('btnFlip'), btnOut: document.getElementById('btnOut'),
    btnServe: document.getElementById('btnServe'),
    result: document.getElementById('result'), rank: document.getElementById('rank'),
    scoreRows: document.getElementById('scoreRows'), resultPay: document.getElementById('resultPay'),
    crossCanvas: document.getElementById('crossCanvas'), crossDoneness: document.getElementById('crossDoneness')
  };

  var toastTimer = null;
  function toast(msg, small) {
    el.toast.textContent = msg;
    el.toast.style.fontSize = small ? '20px' : '30px';
    el.toast.style.opacity = 1;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { el.toast.style.opacity = 0; }, 1400);
  }
  function setHint(msg) { el.hint.textContent = msg; }
  function updateMoney() { el.money.textContent = '¥' + game.money.toLocaleString(); }

  // ---------- 操作(ドラッグ / フリップ / オービット) ----------
  var raycaster = new THREE.Raycaster();
  var pointerNdc = new THREE.Vector2();
  var dragPlane = new THREE.Plane(new THREE.Vector3(0, 1, 0), 0);
  var pointer = { down: false, mode: null, sx: 0, sy: 0, t0: 0, moved: 0 };
  var flipping = false;

  function ndcFromEvent(e) {
    var rect = renderer.domElement.getBoundingClientRect();
    pointerNdc.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
    pointerNdc.y = -((e.clientY - rect.top) / rect.height) * 2 + 1;
  }

  function tryFlip() {
    if (!steak || steak.location !== 'pan' || flipping || game.phase !== 'play') return;
    flipping = true;
    steak.flips++;
    AudioFX.flip();
    var t0 = performance.now();
    var baseY = steakMesh.position.y;
    (function anim() {
      var t = (performance.now() - t0) / 380;
      if (t >= 1) {
        steakMesh.rotation.x = 0;
        steakMesh.position.y = baseY;
        steak.up = 1 - steak.up;
        applyFaceMaterials();
        flipping = false;
        // 着地の油ハネ
        for (var i = 0; i < 14; i++) {
          splatter.spawn({
            x: ZONES.pan.x + rand(-0.9, 0.9), y: 0.45, z: ZONES.pan.z + rand(-0.9, 0.9),
            vx: rand(-1.4, 1.4), vy: rand(1.5, 3.4), vz: rand(-1.4, 1.4),
            life: rand(0.4, 0.8), size: rand(0.05, 0.1), alpha: 0.9, gravity: -9,
            r: 1, g: 0.85, b: 0.55
          });
        }
        if (game.panTemp > 130) AudioFX.burst(3200, 0.3, 0.45);
        return;
      }
      steakMesh.rotation.x = Math.PI * t;
      steakMesh.position.y = baseY + Math.sin(Math.PI * t) * 1.1;
      requestAnimationFrame(anim);
    })();
  }

  function nearestZone(p) {
    var best = null, bd = 1e9;
    Object.keys(ZONES).forEach(function (k) {
      var z = ZONES[k];
      var d = Math.hypot(p.x - z.x, p.z - z.z);
      if (d < bd) { bd = d; best = k; }
    });
    return bd < 2.5 ? best : null;
  }

  renderer.domElement.addEventListener('pointerdown', function (e) {
    AudioFX.resume();
    pointer.down = true; pointer.sx = e.clientX; pointer.sy = e.clientY;
    pointer.t0 = performance.now(); pointer.moved = 0;
    ndcFromEvent(e);
    raycaster.setFromCamera(pointerNdc, camera);
    if (steakMesh && !flipping && game.phase === 'play' && raycaster.intersectObject(steakMesh, false).length) {
      pointer.mode = 'steak';
      renderer.domElement.setPointerCapture(e.pointerId);
    } else {
      pointer.mode = 'orbit';
    }
  });
  renderer.domElement.addEventListener('pointermove', function (e) {
    if (!pointer.down) return;
    var dx = e.clientX - pointer.sx, dy = e.clientY - pointer.sy;
    pointer.moved = Math.max(pointer.moved, Math.abs(dx) + Math.abs(dy));
    if (pointer.mode === 'orbit') {
      camCtl.thetaT = clamp(camCtl.thetaT - dx * 0.005, -1.0, 1.0);
      camCtl.phiT = clamp(camCtl.phiT - dy * 0.004, 0.45, 1.35);
      pointer.sx = e.clientX; pointer.sy = e.clientY;
    } else if (pointer.mode === 'steak' && pointer.moved > 8) {
      steak.location = 'drag';
      ndcFromEvent(e);
      raycaster.setFromCamera(pointerNdc, camera);
      dragPlane.constant = -0.9; // y=0.9 の水平面
      var hit = new THREE.Vector3();
      if (raycaster.ray.intersectPlane(dragPlane, hit)) {
        steakMesh.position.set(clamp(hit.x, -7, 7), 0.9 + steakHalfH, clamp(hit.z, -3.5, 3.6));
      }
      var nz = nearestZone(steakMesh.position);
      Object.keys(rings).forEach(function (k) {
        rings[k].material.opacity = (k === nz) ? 0.85 : 0.3;
      });
    }
  });
  window.addEventListener('pointerup', function (e) {
    if (!pointer.down) return;
    pointer.down = false;
    Object.keys(rings).forEach(function (k) { rings[k].material.opacity = 0; });
    if (pointer.mode === 'steak') {
      var quick = (performance.now() - pointer.t0) < 260 && pointer.moved < 8;
      if (quick) {
        if (steak.location === 'pan') tryFlip();
      } else if (steak.location === 'drag') {
        var nz = nearestZone(steakMesh.position) || 'board';
        dropSteak(nz);
      }
    }
    pointer.mode = null;
  });
  renderer.domElement.addEventListener('wheel', function (e) {
    e.preventDefault();
    camCtl.radiusT = clamp(camCtl.radiusT + e.deltaY * 0.004, 4.5, 14);
  }, { passive: false });

  function dropSteak(zoneKey) {
    var from = steak.location;
    placeSteak(zoneKey);
    if (zoneKey === 'pan') {
      steak.resting = 0;
      steak.oilUsedAtCook = game.oiled;
      if (game.panTemp > 120) {
        AudioFX.burst(3600, 0.5, 0.6);
        toast('ジュゥゥ…!', true);
        for (var i = 0; i < 20; i++) {
          steam.spawn({
            x: steakMesh.position.x + rand(-0.8, 0.8), y: 0.6, z: steakMesh.position.z + rand(-0.6, 0.6),
            vy: rand(0.8, 1.6), life: rand(0.8, 1.6), size: rand(0.35, 0.7), grow: 0.5,
            alpha: 0.5, r: 1, g: 1, b: 1
          });
        }
      } else if (game.panTemp < 80) {
        toast('パンが冷たい…', true);
      }
    }
    if (zoneKey === 'board' && from !== 'board' && maxCook() > 0.3) {
      toast('休ませ中…', true);
    }
  }

  function maxCook() { return Math.max(steak.sides[0].cook, steak.sides[1].cook); }

  // ---------- ボタン ----------
  el.heat.addEventListener('input', function () {
    game.heat = +el.heat.value;
    el.heatVal.textContent = game.heat + '%';
    AudioFX.resume();
  });
  el.btnOil.addEventListener('click', function () {
    AudioFX.resume(); AudioFX.click();
    if (game.oiled) { toast('もう油はある', true); return; }
    game.oiled = true;
    toast('油をひいた');
    if (steak && steak.location === 'pan') steak.oilUsedAtCook = true;
  });
  el.btnButter.addEventListener('click', function () {
    AudioFX.resume(); AudioFX.click();
    if (game.butter > 0) return;
    game.butter = 1;
    butterMesh.visible = true;
    butterMesh.scale.set(1, 1, 1);
    steak.buttered = true;
    toast('バター投入!');
    AudioFX.burst(4000, 0.6, 0.5);
  });
  el.btnSalt.addEventListener('click', function () {
    AudioFX.resume(); AudioFX.click();
    if (!steak || steak.salted) return;
    steak.salted = true;
    steak.saltTiming = maxCook() < 0.05 ? 1 : 0.5;  // 焼く前なら満点
    steak.saltDots.visible = true;
    AudioFX.burst(6000, 0.25, 0.3);
    toast('塩をふった', true);
  });
  el.btnPepper.addEventListener('click', function () {
    AudioFX.resume(); AudioFX.click();
    if (!steak || steak.peppered) return;
    steak.peppered = true;
    steak.pepperDots.visible = true;
    AudioFX.burst(5000, 0.25, 0.3);
    toast('こしょうをふった', true);
  });
  el.btnFlip.addEventListener('click', function () { AudioFX.resume(); tryFlip(); });
  el.btnOut.addEventListener('click', function () {
    AudioFX.resume(); AudioFX.click();
    if (steak.location === 'pan') dropSteak('board');
  });
  el.btnServe.addEventListener('click', function () {
    AudioFX.resume();
    if (steak.location === 'plate') serve();
  });

  document.getElementById('soundBtn').addEventListener('click', function () {
    AudioFX.resume();
    AudioFX.enabled = !AudioFX.enabled;
    this.textContent = AudioFX.enabled ? '🔊 音 ON' : '🔇 音 OFF';
    if (!AudioFX.enabled) AudioFX.setSizzle(0);
  });

  // ---------- 画質(4K対応) ----------
  function applyQuality(mode) {
    var pr;
    if (mode === 'hd') pr = 1;
    else if (mode === '2k') pr = 2560 / window.innerWidth;
    else if (mode === '4k') pr = 3840 / window.innerWidth;
    else pr = Math.min(window.devicePixelRatio || 1, 2);
    pr = clamp(pr, 0.75, 4);
    renderer.setPixelRatio(pr);
    renderer.setSize(window.innerWidth, window.innerHeight);
    camera.aspect = window.innerWidth / window.innerHeight;
    camera.updateProjectionMatrix();
    smoke.mat.uniforms.uPx.value = pr;
    steam.mat.uniforms.uPx.value = pr;
    splatter.mat.uniforms.uPx.value = pr;
  }
  var qualitySel = document.getElementById('quality');
  qualitySel.addEventListener('change', function () {
    applyQuality(qualitySel.value);
    toast('解像度: ' + Math.round(renderer.domElement.width) + '×' + Math.round(renderer.domElement.height), true);
  });
  window.addEventListener('resize', function () { applyQuality(qualitySel.value); });

  // ---------- 採点 ----------
  function donenessName(core) {
    if (core < 40) return '生';
    if (core < 49) return 'レア';
    if (core < 55) return 'ミディアムレア';
    if (core < 60) return 'ミディアム';
    if (core < 66) return 'ミディアムウェル';
    if (core <= 75) return 'ウェルダン';
    return '過加熱';
  }
  function coreColor(core) {
    var stops = [
      [35, [168, 30, 52]], [46, [190, 52, 66]], [52, [214, 96, 100]],
      [58, [206, 130, 116]], [64, [178, 138, 116]], [70, [150, 128, 106]], [80, [128, 112, 96]]
    ];
    if (core <= stops[0][0]) return stops[0][1];
    for (var i = 1; i < stops.length; i++) {
      if (core <= stops[i][0]) {
        var t = (core - stops[i - 1][0]) / (stops[i][0] - stops[i - 1][0]);
        var a = stops[i - 1][1], b = stops[i][1];
        return [lerp(a[0], b[0], t) | 0, lerp(a[1], b[1], t) | 0, lerp(a[2], b[2], t) | 0];
      }
    }
    return stops[stops.length - 1][1];
  }
  function drawCrossSection(core, searAvg, rested) {
    var g = el.crossCanvas.getContext('2d');
    var W = el.crossCanvas.width, H = el.crossCanvas.height;
    g.clearRect(0, 0, W, H);
    var cx = W / 2, cy = H / 2, rx = W * 0.44, ry = H * 0.36;
    // クラスト(外周)
    var crustW = 4 + clamp(searAvg, 0, 1.4) * 8;
    g.fillStyle = searAvg > 1.2 ? '#170d06' : '#5a3010';
    g.beginPath(); g.ellipse(cx, cy, rx + crustW, ry + crustW, 0, 0, 7); g.fill();
    // 断面のグラデーション(端はグレー、中心は焼き加減の色)
    var cc = coreColor(core);
    var edge = coreColor(Math.max(core, 66));
    var grd = g.createRadialGradient(cx, cy, 2, cx, cy, Math.max(rx, ry));
    grd.addColorStop(0, 'rgb(' + cc[0] + ',' + cc[1] + ',' + cc[2] + ')');
    grd.addColorStop(0.72, 'rgb(' + ((cc[0] + edge[0]) / 2 | 0) + ',' + ((cc[1] + edge[1]) / 2 | 0) + ',' + ((cc[2] + edge[2]) / 2 | 0) + ')');
    grd.addColorStop(1, 'rgb(' + edge[0] + ',' + edge[1] + ',' + edge[2] + ')');
    g.fillStyle = grd;
    g.beginPath(); g.ellipse(cx, cy, rx, ry, 0, 0, 7); g.fill();
    // 繊維
    g.strokeStyle = 'rgba(255,255,255,0.10)';
    for (var i = 0; i < 22; i++) {
      g.lineWidth = rand(0.5, 1.4);
      g.beginPath();
      var yy = cy + rand(-ry * 0.85, ry * 0.85);
      g.moveTo(cx - rx * 0.9, yy);
      g.bezierCurveTo(cx - rx * 0.3, yy + rand(-4, 4), cx + rx * 0.3, yy + rand(-4, 4), cx + rx * 0.9, yy);
      g.stroke();
    }
    // 休ませた肉のしっとり感
    if (rested && core < 63) {
      g.fillStyle = 'rgba(255,235,235,0.13)';
      g.beginPath(); g.ellipse(cx - rx * 0.2, cy - ry * 0.25, rx * 0.5, ry * 0.35, -0.3, 0, 7); g.fill();
    }
  }

  function serve() {
    game.phase = 'result';
    AudioFX.ding();
    AudioFX.setSizzle(0);
    var s = steak;
    var target = game.order.c;
    var diff = Math.abs(s.core - target);
    var rows = [];
    function row(label, pts, max, note) {
      rows.push({ label: label, pts: Math.round(pts), max: max, note: note || '' });
      return pts;
    }
    var total = 0;
    // 焼き加減 40点
    var dPts = 40 * clamp(1 - Math.max(0, diff - 3) / 9, 0, 1);
    if (s.core < 35) { dPts = 0; s.servedRaw = true; }
    total += row('焼き加減 (' + donenessName(s.core) + ' / 注文: ' + game.order.name + ')', dPts, 40,
      diff <= 3 ? '完璧!' : (s.core < target ? 'あと少し火を' : '入れすぎた'));
    // 焼き目 20点(各面10)
    function searPts(c) {
      if (c < 0.15) return 0;
      if (c <= 1.05) return 10 * clamp((c - 0.15) / 0.55, 0, 1);
      return 10 * clamp(1 - (c - 1.05) / 0.35, 0, 1);
    }
    var sA = searPts(s.sides[0].cook), sB = searPts(s.sides[1].cook);
    if (!s.oilUsedAtCook) { sA *= 0.55; sB *= 0.55; }
    total += row('焼き目 A面', sA, 10, s.sides[0].cook > 1.3 ? '焦げた…' : '');
    total += row('焼き目 B面', sB, 10, s.sides[1].cook > 1.3 ? '焦げた…' : '');
    // 下味 10点
    total += row('塩', s.salted ? 6 * s.saltTiming : 0, 6, s.salted ? (s.saltTiming === 1 ? '焼く前に◎' : '後がけ') : '振り忘れ');
    total += row('こしょう', s.peppered ? 4 : 0, 4, '');
    // 油・バター
    if (!s.oilUsedAtCook) total += row('油なしで焼いた', -8, 0, '焦げ付き');
    if (s.buttered) total += row('バターの香り', 5, 5, 'アロゼ風');
    // 休ませ 10点
    var restPts = 10 * clamp(s.resting / 45, 0, 1);
    total += row('休ませ (' + Math.round(s.resting) + '秒)', restPts, 10, s.resting < 20 ? '肉汁が流れ出た' : '');
    total = clamp(Math.round(total), 0, 100);

    var rank = total >= 92 ? 'S' : total >= 78 ? 'A' : total >= 62 ? 'B' : total >= 45 ? 'C' : 'D';
    var pay = Math.round((500 + total * 16) * (rank === 'S' ? 1.35 : 1));
    if (s.servedRaw) { rank = 'D'; pay = 0; }
    game.money += pay;
    storage.set('kiwami_money', String(game.money));
    updateMoney();

    el.rank.textContent = rank;
    el.rank.style.color = rank === 'S' ? '#ffd700' : rank === 'A' ? '#e8b34b' : rank === 'B' ? '#b6c98a' : rank === 'C' ? '#9aa' : '#c66';
    el.crossDoneness.textContent = donenessName(s.core) + '(中心 ' + Math.round(s.core) + '°C)';
    drawCrossSection(s.core, (s.sides[0].cook + s.sides[1].cook) / 2, s.resting >= 30);
    el.scoreRows.innerHTML = rows.map(function (r) {
      return '<div class="scoreRow"><span>' + r.label +
        (r.note ? ' <span style="color:#b6a488;font-size:11px">' + r.note + '</span>' : '') +
        '</span><b>' + r.pts + (r.max ? ' / ' + r.max : '') + '</b></div>';
    }).join('') +
      '<div class="scoreRow" style="border-bottom:none;font-size:15px"><span>合計</span><b>' + total + ' / 100</b></div>';
    el.resultPay.textContent = s.servedRaw ? '生です!お代は頂けません…' : '報酬 ¥' + pay.toLocaleString();
    el.result.style.display = 'flex';
  }

  document.getElementById('nextBtn').addEventListener('click', function () {
    AudioFX.click();
    el.result.style.display = 'none';
    game.phase = 'play';
    game.oiled = false; game.butter = 0;
    oilMesh.material.opacity = 0;
    oilMesh.material.color.setHex(0x2e2306);
    butterMesh.visible = false;
    garnish.visible = false;
    newSteak();
    newOrder();
    setHint('🥩 新しい肉が来た。まずは塩こしょう&パンの予熱から');
  });

  // ---------- シミュレーション ----------
  var lastHint = '';
  function autoHint() {
    var h;
    if (game.phase !== 'play') h = '';
    else if (steak.location === 'board' && maxCook() < 0.05) {
      h = game.panTemp < 120 ? '🔥 火力を上げてパンを予熱(180°C↑が目安)。塩こしょうも忘れずに'
        : '🥩 肉をドラッグしてフライパンへ!';
    } else if (steak.location === 'pan') {
      var down = steak.sides[1 - steak.up].cook;
      h = down < 0.55 ? '🍳 下面を焼いている… クリックで返せる'
        : down < 1.05 ? '✨ いい焼き目!そろそろ返すか取り出す'
          : '⚠️ 焦げ始めている!早く返すか取り出せ!';
    } else if (steak.location === 'board' && maxCook() >= 0.05) {
      h = steak.resting < 45 ? '⏳ 休ませ中… 45秒(ゲーム内)休ませると肉汁が落ち着く'
        : '🍽️ 皿にドラッグして盛り付けよう';
    } else if (steak.location === 'plate') {
      h = '🛎️ 「提供する」で勝負!';
    } else h = lastHint;
    if (h !== lastHint) { setHint(h); lastHint = h; }
  }

  function simulate(dt) {
    var sim = dt * TIMESCALE;
    var steakInPan = steak && steak.location === 'pan';

    // パン温度
    var targetPan = AMBIENT_C + game.heat / 100 * 265 - (steakInPan ? 18 : 0);
    game.panTemp += (targetPan - game.panTemp) * (1 - Math.exp(-0.055 * sim));

    // バターが溶ける
    if (game.butter > 0 && game.butter < 2 && game.panTemp > 60) {
      butterMesh.scale.multiplyScalar(Math.max(0.001, 1 - 0.35 * sim * 0.2));
      butterMesh.position.y = 0.1 + butterMesh.scale.y * 0.1;
      if (butterMesh.scale.x < 0.12) {
        game.butter = 2;
        butterMesh.visible = false;
        oilMesh.material.color.setHex(0x6a4c10);
        if (!game.oiled) { game.oiled = true; if (steakInPan) steak.oilUsedAtCook = true; }
      }
    }
    // 油の見た目
    var oilTarget = game.oiled ? (game.butter === 2 ? 0.55 : 0.42) : 0;
    oilMesh.material.opacity += (oilTarget - oilMesh.material.opacity) * Math.min(1, dt * 3);

    var sizzle = 0;
    if (steak) {
      var down = steak.sides[1 - steak.up];
      if (steakInPan && !flipping) {
        // 接地面のメイラード反応
        if (game.panTemp > 130) {
          var rate = (game.panTemp - 130) / 130 * 0.016 * (steak.oilUsedAtCook ? 1 : 0.6) * (game.butter === 2 ? 1.15 : 1);
          down.cook += rate * sim;
          steak.moisture = Math.max(0, steak.moisture - rate * 0.3 * sim);
        }
        // 中心温度
        var eq = Math.min(game.panTemp, 205) * 0.62 + AMBIENT_C * 0.38;
        if (eq > steak.core) steak.core += (eq - steak.core) * 0.0028 * sim;
        steak.carry = clamp((game.panTemp - 120) / 40, 0, 3.5);
        sizzle = clamp((game.panTemp - 110) / 130, 0, 1) * (0.35 + 0.65 * steak.moisture) * (steak.oilUsedAtCook ? 1 : 0.55);
      } else if (steak.location === 'board' && maxCook() > 0.05) {
        // 休ませ:余熱で中心温度が少し上がってから下がる
        steak.resting += sim;
        if (steak.carry > 0) {
          var boost = Math.min(steak.carry, 0.35 * sim);
          steak.core += boost; steak.carry -= boost;
        } else {
          steak.core = Math.max(AMBIENT_C, steak.core - 0.06 * sim);
        }
      } else if (steak.location === 'plate') {
        steak.core = Math.max(AMBIENT_C, steak.core - 0.05 * sim);
      }
      // テクスチャ更新
      steak.sides[0].painter.update(steak.sides[0].cook);
      steak.sides[1].painter.update(steak.sides[1].cook);
      steak.sidePainter.update((steak.sides[0].cook + steak.sides[1].cook) / 2);
    }
    // 油だけでも高温ならわずかに鳴く
    if (!steakInPan && game.oiled && game.panTemp > 170) sizzle = Math.max(sizzle, 0.12);
    if (game.butter === 1 && butterMesh.visible && game.panTemp > 80) sizzle = Math.max(sizzle, 0.3);
    game.sizzle = sizzle;
    AudioFX.setSizzle(sizzle);
    if (AudioFX.ctx && sizzle > 0.15 && Math.random() < sizzle * dt * 14) {
      AudioFX.burst(rand(2400, 6400), rand(0.03, 0.09), rand(0.1, 0.3) * sizzle);
    }
  }

  function spawnParticles(dt) {
    var steakInPan = steak && steak.location === 'pan';
    var px = ZONES.pan.x, pz = ZONES.pan.z;
    // 煙(焼き進行と焦げで発生)
    if (steakInPan) {
      var down = steak.sides[1 - steak.up];
      var burnFactor = clamp((down.cook - 0.9) / 0.5, 0, 1);
      var smokeRate = (clamp((game.panTemp - 170) / 100, 0, 1) * 4 + burnFactor * 22) * (0.5 + down.cook);
      if (Math.random() < smokeRate * dt) {
        var shade = lerp(0.75, 0.2, burnFactor);
        smoke.spawn({
          x: steakMesh.position.x + rand(-0.8, 0.8), y: 0.62, z: steakMesh.position.z + rand(-0.6, 0.6),
          vx: rand(-0.08, 0.14), vy: rand(0.55, 1.0), vz: rand(-0.08, 0.08),
          life: rand(2.2, 3.8), size: rand(0.5, 0.9), grow: 0.55,
          alpha: lerp(0.16, 0.4, burnFactor), r: shade, g: shade, b: shade
        });
      }
      // 湯気
      if (steak.moisture > 0.15 && game.panTemp > 120 && Math.random() < 6 * dt * steak.moisture) {
        steam.spawn({
          x: steakMesh.position.x + rand(-0.7, 0.7), y: 0.6, z: steakMesh.position.z + rand(-0.5, 0.5),
          vy: rand(0.7, 1.3), life: rand(0.7, 1.4), size: rand(0.3, 0.55), grow: 0.45,
          alpha: 0.3, r: 1, g: 1, b: 1
        });
      }
      // 油ハネ
      if (game.oiled && game.panTemp > 190 && Math.random() < game.sizzle * 8 * dt) {
        splatter.spawn({
          x: px + rand(-1.1, 1.1), y: 0.42, z: pz + rand(-1.1, 1.1),
          vx: rand(-1.2, 1.2), vy: rand(1.2, 3), vz: rand(-1.2, 1.2),
          life: rand(0.35, 0.7), size: rand(0.04, 0.09), alpha: 0.85, gravity: -9,
          r: 1, g: 0.82, b: 0.5
        });
      }
    } else if (game.oiled && game.panTemp > 230 && Math.random() < 2.5 * dt) {
      // 空焼きの煙
      smoke.spawn({
        x: px + rand(-0.9, 0.9), y: 0.5, z: pz + rand(-0.9, 0.9),
        vy: rand(0.5, 0.9), life: rand(2, 3.2), size: rand(0.4, 0.7), grow: 0.5,
        alpha: 0.18, r: 0.6, g: 0.6, b: 0.6
      });
    }
  }

  function updateFlames(dt, time) {
    var h = game.heat / 100;
    for (var i = 0; i < flames.length; i++) {
      var f = flames[i];
      var flick = 0.82 + 0.18 * Math.sin(time * 17 + f.phase * 7) * Math.sin(time * 23 + f.phase * 3);
      var sc = h * flick;
      if (f.blue) f.s.scale.set(0.5 * sc + 0.001, 0.55 * sc + 0.001, 1);
      else f.s.scale.set(0.38 * sc + 0.001, (0.7 + 0.25 * flick) * sc + 0.001, 1);
      f.s.material.opacity = f.blue ? h * 0.9 : h * 0.55 * flick;
    }
    flameLight.intensity = h * (1.1 + 0.35 * Math.sin(time * 31)) * 0.9;
    glowRing.material.opacity = h * (0.16 + 0.05 * Math.sin(time * 21));
    panMesh.material.emissiveIntensity = clamp((game.panTemp - 190) / 450, 0, 0.16);
  }

  function updateButtons() {
    var inPan = steak && steak.location === 'pan' && game.phase === 'play';
    el.btnFlip.disabled = !inPan || flipping;
    el.btnOut.disabled = !inPan || flipping;
    el.btnServe.disabled = !(steak && steak.location === 'plate' && game.phase === 'play');
    el.btnButter.disabled = !(game.panTemp > 80 && game.butter === 0 && game.phase === 'play');
    el.btnOil.disabled = game.oiled || game.phase !== 'play';
    el.btnSalt.disabled = !steak || steak.salted || game.phase !== 'play';
    el.btnPepper.disabled = !steak || steak.peppered || game.phase !== 'play';
  }

  function updateGauges() {
    el.panTempVal.textContent = Math.round(game.panTemp) + '°C';
    el.panBar.style.width = clamp((game.panTemp - 20) / 260 * 100, 0, 100) + '%';
    if (steak) {
      el.coreTempVal.textContent = Math.round(steak.core) + '°C';
      el.coreBar.style.width = clamp(steak.core / 90 * 100, 0, 100) + '%';
      el.searA.style.width = clamp(steak.sides[0].cook / 1.4 * 100, 0, 100) + '%';
      el.searB.style.width = clamp(steak.sides[1].cook / 1.4 * 100, 0, 100) + '%';
      var showRest = steak.location === 'board' && maxCook() > 0.05;
      el.restWrap.style.display = showRest ? 'block' : 'none';
      if (showRest) {
        el.restBar.style.width = clamp(steak.resting / 45 * 100, 0, 100) + '%';
        el.restVal.textContent = Math.round(steak.resting) + 's / 45s';
      }
    }
  }

  // ---------- メインループ ----------
  var clock = new THREE.Clock();
  var uiTimer = 0;
  function loop() {
    requestAnimationFrame(loop);
    var dt = Math.min(clock.getDelta(), 0.1);
    var time = clock.elapsedTime;
    if (game.phase === 'play') simulate(dt);
    spawnParticles(dt);
    smoke.update(dt, true);
    steam.update(dt, true);
    splatter.update(dt, false);
    updateFlames(dt, time);
    camCtl.apply();
    uiTimer -= dt;
    if (uiTimer <= 0) {
      uiTimer = 0.12;
      updateGauges();
      updateButtons();
      autoHint();
    }
    renderer.render(scene, camera);
  }

  // ---------- 起動 ----------
  applyQuality('auto');
  updateMoney();
  newSteak();
  newOrder();
  loop();
  requestAnimationFrame(function () {
    requestAnimationFrame(function () {
      var l = document.getElementById('loading');
      l.style.transition = 'opacity .6s';
      l.style.opacity = 0;
      setTimeout(function () { l.remove(); }, 700);
    });
  });
})();
