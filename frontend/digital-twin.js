/* ============================================================
   DIGITAL TWIN MODULE — Salle Informatique 3D
   ------------------------------------------------------------
   Reads : window.SEAT_DATA (seat ids + chair positions)
           window.CLASSROOM_CONFIG (room, layout, doors, twin)
           window.__TW_SCENE (the THREE.Scene)
   Does  : animated student entry (door -> aisle -> chair),
           seated avatars, WebSocket client (Raspberry Pi),
           Test Mode panel with door picker + per-seat buttons.

   Protocol (Pi -> browser) — backwards compatible :
     {"type":"occupancy","seats":["C2-R3","C3-R1"]}       (full state)
     {"type":"seat_event","action":"enter"|"leave",
      "seat":"C2-R4","door":"door1"|"door2"|null,
      "track":17,"ts":123.4,"event_id":42}               (optional event)

   The browser animates on BOTH: a seat_event only supplies the
   entrance hint; the occupancy snapshot remains the source of truth.

   Avatar state machine :
     WAITING -> ENTERING -> WALKING_TO_SEAT -> TURNING
             -> SITTING -> SEATED
     SEATED -> STANDING -> WALKING_OUT -> (removed)
   ============================================================ */
(function () {
  'use strict';

  const DEFAULTS = {
    walkSpeed: 1.45,            // m/s
    aisleInset: 0.9,            // side aisle distance from the wall
    crossAisleOffset: 0.62,     // path z behind the target row's chairs
    approachOffset: 0.50,       // stop point just behind the chair
    seatOffsetZ: 0.00,          // final hips offset from the chair centre
    popMs: 320,                 // fallback seated pop duration
    sitMs: 900,
    standMs: 700,
    turnMs: 520,
    graceMs: 3500,              // live seat empty this long before leaving
    entryFallbackMs: 9000,      // never leave a student stuck off-screen
    stalledFrameMs: 500,        // animation loop considered stalled after this
    fallbackDoor: null,         // 'door1' | 'door2' | null
    assumeNearestDoorWhenUnknown: false
  };

  const AV = {
    hipY: 0.90,
    seatRigY: -0.34,            // seated hips at ~0.56 m (chair seat top = 0.5025 m)
    torsoH: 0.54,
    thigh: 0.42,
    shin: 0.42,
    hipX: 0.085,
    shoulderX: 0.19,
    shoulderY: 0.50,
    headY: 0.75
  };

  const RED = {
    light: 0xa72f2f,
    main:  0x841a19,
    dark:  0x5a1012,
    deep:  0x340708,
    hair:  0x3b090b,
    face:  0x170203
  };

  const GEO = {
    head: new THREE.SphereGeometry(0.13, 16, 14),
    hair: new THREE.SphereGeometry(0.136, 16, 12, 0, Math.PI * 2, 0, Math.PI * 0.58),
    eye: new THREE.SphereGeometry(0.014, 8, 8),
    hand: new THREE.SphereGeometry(0.045, 8, 8)
  };

  function hashStr(s) {
    let h = 2166136261;
    for (let i = 0; i < s.length; i++) {
      h ^= s.charCodeAt(i);
      h = Math.imul(h, 16777619);
    }
    return h >>> 0;
  }

  function buildAvatar(seed) {
    const skinMat  = new THREE.MeshStandardMaterial({ color: RED.light, roughness: 0.85 });
    const shirtMat = new THREE.MeshStandardMaterial({ color: RED.main,  roughness: 0.92 });
    const pantsMat = new THREE.MeshStandardMaterial({ color: RED.dark,  roughness: 0.92 });
    const shoeMat  = new THREE.MeshStandardMaterial({ color: RED.deep,  roughness: 0.85 });
    const hairMat  = new THREE.MeshStandardMaterial({ color: RED.hair,  roughness: 0.95 });

    const root = new THREE.Group();
    const rig = new THREE.Group();
    root.add(rig);

    // Hips / body pivot
    const hips = new THREE.Group();
    hips.position.y = AV.hipY;
    rig.add(hips);

    // Torso
    const torso = new THREE.Mesh(
      new THREE.CylinderGeometry(0.15, 0.168, AV.torsoH, 10),
      shirtMat
    );
    torso.position.y = AV.torsoH / 2;
    hips.add(torso);

    // Neck + head (model faces -Z, like the chairs)
    const neck = new THREE.Mesh(new THREE.CylinderGeometry(0.052, 0.06, 0.08, 8), skinMat);
    neck.position.y = AV.torsoH + 0.01;
    hips.add(neck);

    const head = new THREE.Mesh(GEO.head, skinMat);
    head.position.y = AV.headY;
    head.scale.set(1, 1.05, 0.94);
    hips.add(head);

    // Hair
    const hair = new THREE.Mesh(GEO.hair, hairMat);
    hair.position.y = AV.headY + 0.005;
    hips.add(hair);

    // Eyes + mouth (front = -Z)
    const faceMat = new THREE.MeshStandardMaterial({ color: RED.face, roughness: 0.65 });
    [-0.045, 0.045].forEach(ex => {
      const eye = new THREE.Mesh(GEO.eye, faceMat);
      eye.position.set(ex, AV.headY + 0.025, -0.118);
      hips.add(eye);
    });
    const mouth = new THREE.Mesh(
      new THREE.BoxGeometry(0.05, 0.012, 0.01),
      faceMat
    );
    mouth.position.set(0, AV.headY - 0.05, -0.12);
    hips.add(mouth);

    function makeArm(side) {
      const shoulder = new THREE.Group();
      shoulder.position.set(side * AV.shoulderX, AV.shoulderY, 0);
      shoulder.rotation.z = -side * 0.07;

      const upper = new THREE.Mesh(
        new THREE.CylinderGeometry(0.05, 0.045, 0.28, 8),
        shirtMat
      );
      upper.position.y = -0.14;
      shoulder.add(upper);

      const elbow = new THREE.Group();
      elbow.position.y = -0.28;
      shoulder.add(elbow);

      const fore = new THREE.Mesh(
        new THREE.CylinderGeometry(0.043, 0.035, 0.26, 8),
        skinMat
      );
      fore.position.y = -0.13;
      elbow.add(fore);

      const hand = new THREE.Mesh(GEO.hand, skinMat);
      hand.position.y = -0.27;
      elbow.add(hand);

      hips.add(shoulder);
      return { shoulder, elbow };
    }

    function makeLeg(side) {
      const hip = new THREE.Group();
      hip.position.set(side * AV.hipX, 0, 0);

      const thigh = new THREE.Mesh(
        new THREE.CylinderGeometry(0.073, 0.064, AV.thigh, 8),
        pantsMat
      );
      thigh.position.y = -AV.thigh / 2;
      hip.add(thigh);

      const knee = new THREE.Group();
      knee.position.y = -AV.thigh;
      hip.add(knee);

      const shin = new THREE.Mesh(
        new THREE.CylinderGeometry(0.06, 0.048, AV.shin, 8),
        pantsMat
      );
      shin.position.y = -AV.shin / 2;
      knee.add(shin);

      const shoe = new THREE.Mesh(new THREE.BoxGeometry(0.115, 0.075, 0.23), shoeMat);
      shoe.position.set(0, -AV.shin - 0.01, -0.06);
      knee.add(shoe);

      hips.add(hip);
      return { hip, knee };
    }

    const armL = makeArm(-1), armR = makeArm(1);
    const legL = makeLeg(-1), legR = makeLeg(1);

    root.traverse(o => { if (o.isMesh) o.castShadow = true; });

    const heightScale = 1.0;
    root.scale.setScalar(heightScale);

    return {
      root, rig,
      joints: {
        shoulderL: armL.shoulder, elbowL: armL.elbow,
        shoulderR: armR.shoulder, elbowR: armR.elbow,
        hipL: legL.hip, kneeL: legL.knee,
        hipR: legR.hip, kneeR: legR.knee
      },
      baseScale: heightScale
    };
  }

  /* ================= BOOT ================= */
  function boot(scene, SEATS) {
    if (!scene || !SEATS.length) {
      console.error('[Twin] scene or SEAT_DATA missing');
      return;
    }

    const CC = window.CLASSROOM_CONFIG || {};
    const ROOM = CC.room || { w: 13.6, d: 19.5, h: 4.4 };
    const DOORS = CC.doors || {};
    const CFG = Object.assign({}, DEFAULTS, CC.twin || {});

    const seatById = new Map(SEATS.map(s => [s.id, s]));
    const doorIds = Object.keys(DOORS);
    const AISLE = {
      leftOuter:  -ROOM.w / 2 + CFG.aisleInset,
      rightOuter:  ROOM.w / 2 - CFG.aisleInset
    };

    function normalizeDoorId(d) {
      if (d === 1 || d === '1' || d === 'door1') return 'door1';
      if (d === 2 || d === '2' || d === 'door2') return 'door2';
      if (typeof d === 'string' && DOORS[d]) return d;
      return null;
    }

    function resolveDoor(seat, requested) {
      const req = normalizeDoorId(requested);
      if (req) return req;
      if (CFG.fallbackDoor && DOORS[CFG.fallbackDoor]) return CFG.fallbackDoor;
      if (CFG.assumeNearestDoorWhenUnknown && doorIds.length) {
        let best = null, bd = Infinity;
        doorIds.forEach(id => {
          const p = DOORS[id].inside || DOORS[id].outside;
          if (!p) return;
          const d = (p.x - seat.x) ** 2 + (p.z - seat.z) ** 2;
          if (d < bd) { bd = d; best = id; }
        });
        return best;
      }
      return null;
    }

    function buildEntryPath(door, seat) {
      const inward = (door.world && door.world.inward) || door.inward || { x: 1, z: 0 };
      const start = door.outside || { x: seat.x, z: seat.z + 2 };
      const inside = door.inside || { x: start.x + inward.x * 1.5, z: start.z + inward.z * 1.5 };
      // Pick the side aisle on the same side of the room as the door.
      const doorX = (door.world && door.world.x) || inside.x;
      const sideAisle = doorX < 0 ? AISLE.leftOuter : AISLE.rightOuter;
      const crossZ = seat.z + CFG.crossAisleOffset;       // behind that row's chairs
      const approachZ = seat.z + CFG.approachOffset;      // just behind the chair
      return [
        { x: inside.x, z: inside.z },
        { x: sideAisle, z: inside.z },
        { x: sideAisle, z: crossZ },
        { x: seat.x, z: crossZ },
        { x: seat.x, z: approachZ }
      ];
    }

    /* ================= POSE HELPERS ================= */
    function poseStand(a) {
      const j = a.joints;
      j.hipL.rotation.x = j.hipR.rotation.x = 0;
      j.kneeL.rotation.x = j.kneeR.rotation.x = 0;
      j.shoulderL.rotation.x = j.shoulderR.rotation.x = 0;
      j.elbowL.rotation.x = j.elbowR.rotation.x = 0;
      a.rig.position.y = 0;
    }

    function poseWalk(a) {
      const j = a.joints;
      const s = Math.sin(a.walkPhase);
      j.hipL.rotation.x = s * 0.55;
      j.hipR.rotation.x = -s * 0.55;
      j.kneeL.rotation.x = -Math.max(0, s) * 0.72;
      j.kneeR.rotation.x = -Math.max(0, -s) * 0.72;
      j.shoulderL.rotation.x = -s * 0.45;
      j.shoulderR.rotation.x = s * 0.45;
      j.elbowL.rotation.x = -0.18 - Math.max(0, s) * 0.28;
      j.elbowR.rotation.x = -0.18 - Math.max(0, -s) * 0.28;
      a.rig.position.y = Math.abs(s) * 0.018;
    }

    function poseSit(a, amount) {
      const j = a.joints;
      j.hipL.rotation.x = j.hipR.rotation.x = 1.35 * amount;
      j.kneeL.rotation.x = j.kneeR.rotation.x = -1.35 * amount;
      j.shoulderL.rotation.x = j.shoulderR.rotation.x = 1.30 * amount;
      j.elbowL.rotation.x = j.elbowR.rotation.x = -0.30 * amount;
      a.rig.position.y = AV.seatRigY * amount;
    }

    /*
     * Place an actor directly in its chair, fully visible.
     *
     * The entry animation is driven by requestAnimationFrame. Browsers pause
     * or heavily throttle that loop when the tab is in the background, the
     * window is occluded, or the page runs in a preview pane. In those cases
     * a student created at the door could stay off-screen forever even though
     * the backend already confirmed the seat. This helper makes the avatar
     * appear at the correct position regardless of frame timing.
     */
    function seatImmediately(a) {
      a.state = 'SEATED';
      a.stateT = 0;
      a.path = null;
      a.pathIndex = 0;
      a.delay = 0;
      a.leaving = false;
      a.pendingLeave = false;
      a.sitStart = null;
      a.sitEnd = null;
      a.popT = null;
      a.avatar.visible = true;
      a.avatar.position.set(a.seat.x, 0, a.seat.z + CFG.seatOffsetZ);
      a.avatar.rotation.y = 0;
      a.avatar.scale.setScalar(a.baseScale);
      poseSit(a, 1);
    }

    const lerp = (a, b, t) => a + (b - a) * t;
    const easeInOut = t => t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
    function angleDiff(a, b) {
      return Math.atan2(Math.sin(b - a), Math.cos(b - a));
    }
    function dampAngle(cur, target, dt, speed) {
      return cur + angleDiff(cur, target) * (1 - Math.exp(-speed * dt));
    }

    /* ================= ACTORS ================= */
    const actors = {};          // seatId -> actor

    function buildActor(seat, door, opts) {
      opts = opts || {};
      const seed = hashStr(seat.id);
      const av = buildAvatar(seed);

      const a = {
        seatId: seat.id,
        seat: seat,
        avatar: av.root,
        rig: av.rig,
        joints: av.joints,
        baseScale: av.baseScale,
        doorId: door ? (door.id || null) : null,
        door: door || null,
        path: null,
        pathIndex: 0,
        state: 'WAITING',
        stateT: 0,
        delay: Math.max(0, opts.delay || 0),
        walkPhase: 0,
        targetYaw: 0,
        leaving: false,
        pendingLeave: false,
        popT: null,
        popDur: CFG.popMs,
        sitStart: null,
        sitEnd: null,
        fallbackTimer: null,
        phase: (seed % 1000) / 1000 * Math.PI * 2
      };

      scene.add(a.avatar);
      actors[seat.id] = a;

      const stalled = !isFrameLoopAlive();
      if (door && !stalled) {
        const start = door.outside || { x: seat.x, z: seat.z + 2 };
        const inside = door.inside || start;
        a.avatar.position.set(start.x, 0, start.z);
        a.avatar.rotation.y = Math.atan2(-(inside.x - start.x), -(inside.z - start.z));
        a.avatar.visible = a.delay <= 0;
        a.path = buildEntryPath(door, seat);
        a.pathIndex = 0;
        poseStand(a);

        /*
         * Safety net: if the animation loop stalls (background tab, occluded
         * window, preview pane), the student must still end up visible in the
         * chair instead of remaining at the off-screen door forever.
         *
         * If the loop is running, the walk is allowed to finish normally:
         * seating a far seat after a fixed delay would teleport a student who
         * is still legitimately walking. We re-check instead.
         */
        function checkEntry() {
          if (actors[seat.id] !== a || a.state === 'SEATED' || a.leaving) return;
          if (!isFrameLoopAlive()) {
            a.fallbackTimer = null;
            seatImmediately(a);
            return;
          }
          a.fallbackTimer = setTimeout(checkEntry, 3000);
        }
        a.fallbackTimer = setTimeout(checkEntry, CFG.entryFallbackMs);
      } else if (stalled) {
        // The render loop is not running: skip the off-screen entrance and
        // show the student seated right away.
        seatImmediately(a);
      } else {
        // Honest fallback: no entrance evidence -> appear directly seated.
        a.avatar.position.set(seat.x, 0, seat.z + CFG.seatOffsetZ);
        a.avatar.rotation.y = 0;
        a.avatar.scale.setScalar(0.01);
        a.popT = 0;
        poseSit(a, 1);
        a.state = 'SEATED';
      }
      updateButtonStates();
      return a;
    }

    function removeActor(seatId) {
      const a = actors[seatId];
      if (!a) return;
      if (a.fallbackTimer) clearTimeout(a.fallbackTimer);
      scene.remove(a.avatar);
      delete actors[seatId];
      updateButtonStates();
    }

    function startStanding(a) {
      a.state = 'STANDING';
      a.stateT = 0;
      a.leaving = true;
      a.pendingLeave = false;
    }

    function startLeavingWalk(a) {
      if (!a.path) { removeActor(a.seatId); return; }
      const pos = a.avatar.position;
      const rev = a.path.slice().reverse();
      a.path = [{ x: pos.x, z: pos.z }].concat(rev);
      a.pathIndex = 0;
      a.state = 'WALKING_OUT';
      a.stateT = 0;
      a.leaving = true;
      a.pendingLeave = false;
      a.sitStart = a.sitEnd = null;
    }

    function moveAlongPath(a, dt) {
      if (!a.path || a.pathIndex >= a.path.length) {
        if (a.state === 'WALKING_OUT') removeActor(a.seatId);
        else arriveAtSeat(a);
        return;
      }
      const target = a.path[a.pathIndex];
      const pos = a.avatar.position;
      const dx = target.x - pos.x, dz = target.z - pos.z;
      const dist = Math.hypot(dx, dz);

      if (dist < 0.05) {
        a.pathIndex++;
        return;
      }

      const step = Math.min(dist, CFG.walkSpeed * dt);
      pos.x += dx / dist * step;
      pos.z += dz / dist * step;

      a.targetYaw = Math.atan2(-dx, -dz);
      a.avatar.rotation.y = dampAngle(a.avatar.rotation.y, a.targetYaw, dt, 9);
      a.walkPhase += dt * CFG.walkSpeed * 7.5;
      poseWalk(a);

      if (a.state === 'ENTERING' && a.pathIndex >= 2) {
        a.state = 'WALKING_TO_SEAT';
      }
    }

    function arriveAtSeat(a) {
      a.state = 'TURNING';
      a.stateT = 0;
      a.targetYaw = 0;
      poseStand(a);
    }

    function updateActor(a, dt, now) {
      if (!a) return;
      a.stateT += dt;

      switch (a.state) {
        case 'WAITING':
          if (a.delay > 0) {
            a.delay -= dt * 1000;
            if (a.delay > 0) break;
          }
          a.avatar.visible = true;
          a.state = 'ENTERING';
          a.stateT = 0;
          break;

        case 'ENTERING':
        case 'WALKING_TO_SEAT':
          moveAlongPath(a, dt);
          break;

        case 'TURNING': {
          a.avatar.rotation.y = dampAngle(a.avatar.rotation.y, 0, dt, 8);
          poseStand(a);
          if (a.stateT * 1000 >= CFG.turnMs ||
              Math.abs(angleDiff(a.avatar.rotation.y, 0)) < 0.02) {
            a.state = 'SITTING';
            a.stateT = 0;
            a.sitStart = { x: a.avatar.position.x, z: a.avatar.position.z };
            a.sitEnd = { x: a.seat.x, z: a.seat.z + CFG.seatOffsetZ };
          }
          break;
        }

        case 'SITTING': {
          const k = Math.min(1, (a.stateT * 1000) / CFG.sitMs);
          const e = easeInOut(k);
          if (a.sitStart && a.sitEnd) {
            a.avatar.position.x = lerp(a.sitStart.x, a.sitEnd.x, e);
            a.avatar.position.z = lerp(a.sitStart.z, a.sitEnd.z, e);
          }
          a.avatar.rotation.y = dampAngle(a.avatar.rotation.y, 0, dt, 12);
          poseSit(a, e);
          if (k >= 1) {
            a.state = 'SEATED';
            a.stateT = 0;
            if (a.pendingLeave) startStanding(a);
          }
          break;
        }

        case 'SEATED':
          poseSit(a, 1);
          a.rig.position.y = AV.seatRigY + Math.sin(now / 1000 * 1.6 + a.phase) * 0.006;
          if (a.pendingLeave) startStanding(a);
          break;

        case 'STANDING': {
          const k = Math.min(1, (a.stateT * 1000) / CFG.standMs);
          poseSit(a, 1 - easeInOut(k));
          if (k >= 1) {
            if (a.doorId && a.path) startLeavingWalk(a);
            else removeActor(a.seatId);
          }
          break;
        }

        case 'WALKING_OUT':
          moveAlongPath(a, dt);
          break;
      }

      if (a.popT !== null) {
        a.popT += dt * 1000;
        const k = Math.min(1, a.popT / a.popDur);
        const e = 1 - Math.pow(1 - k, 3);
        a.avatar.scale.setScalar(a.baseScale * e);
        if (k >= 1) a.popT = null;
      }
    }

    /* ================= PUBLIC ACTIONS ================= */
    let selectedDoor = doorIds[0] || null;

    function spawnStudent(seatId, requestedDoor, opts) {
      const seat = seatById.get(seatId);
      if (!seat) return false;
      const existing = actors[seatId];
      if (existing) {
        if (existing.leaving) removeActor(seatId);
        else return false;                  // duplicate prevented
      }
      const doorId = resolveDoor(seat, requestedDoor);
      const door = doorId ? DOORS[doorId] : null;
      if (door) door.id = door.id || doorId;
      buildActor(seat, door, opts);
      return true;
    }

    function dismissStudent(seatId) {
      const a = actors[seatId];
      if (!a || a.leaving) return false;
      a.leaving = true;
      a.pendingLeave = true;

      /*
       * If the animation loop is paused, waiting for a stand-up / walk-out
       * animation would leave a stale avatar in the scene indefinitely.
       * Vacancy must win immediately when we cannot animate the exit.
       */
      if (!isFrameLoopAlive()) {
        removeActor(seatId);
        return true;
      }

      if (a.state === 'WAITING') {
        removeActor(seatId);
      } else if (a.state === 'SEATED') {
        startStanding(a);
      } else if (a.state === 'SITTING') {
        // finish sitting, then STANDING (handled on completion)
        a.pendingLeave = true;
      } else if (a.state === 'TURNING' || a.state === 'ENTERING' ||
                 a.state === 'WALKING_TO_SEAT') {
        startLeavingWalk(a);
      } else if (a.state === 'STANDING') {
        // already standing
      }
      return true;
    }

    function setOccupied(id, occupied, doorId) {
      if (!seatById.has(id)) return false;
      if (occupied) return spawnStudent(id, doorId || selectedDoor);
      return dismissStudent(id);
    }

    function applyState(seatList, doorId, opts) {
      opts = opts || {};
      const set = new Set((seatList || []).filter(id => seatById.has(id)));
      SEATS.forEach((s, i) => {
        if (set.has(s.id)) {
          if (!actors[s.id]) {
            spawnStudent(s.id, doorId || selectedDoor, {
              delay: opts.immediate ? 0 : i * 70
            });
          }
        } else if (actors[s.id]) {
          dismissStudent(s.id);
        }
      });
    }

    /* ================= ANIMATION LOOP ================= */
    let lastT = performance.now();
    let lastFrameAt = lastT;

    /*
     * True while requestAnimationFrame is actually delivering frames. It is
     * false in background/occluded tabs and in some embedded preview panes,
     * where the browser pauses or heavily throttles rendering.
     */
    function isFrameLoopAlive() {
      return (performance.now() - lastFrameAt) < CFG.stalledFrameMs;
    }

    function tick(now) {
      requestAnimationFrame(tick);
      lastFrameAt = now;
      const dt = Math.min(0.05, (now - lastT) / 1000);
      lastT = now;
      Object.keys(actors).forEach(id => updateActor(actors[id], dt, now));
    }
    requestAnimationFrame(tick);

    /* ================= WEBSOCKET CLIENT ================= */
    const LS_URL = 'twinWsUrl';
    let ws = null;
    let wsEnabled = localStorage.getItem('twinWsEnabled') === '1';
    let mode = 'test';                     // 'test' | 'live'
    let reconnectTimer = null;
    let manualOccupancy = {};
    let liveSeats = new Set();
    const leaveTimers = {};
    const pendingDoor = {};
    const pendingDoorAt = {};
    const seenEventIds = new Set();

    const $ = (id) => document.getElementById(id);

    function setStatus(txt, color) {
      if ($('twin-dot')) $('twin-dot').style.background = color || '#e67e22';
      if ($('twin-status')) $('twin-status').textContent = txt;
    }

    function setMode(m) {
      mode = m;
      document.querySelectorAll('.twin-seat').forEach(b => b.disabled = (m === 'live'));
      if ($('twin-live-note')) $('twin-live-note').style.display = (m === 'live') ? 'inline' : 'none';
      if ($('twin-ws-btn')) $('twin-ws-btn').textContent = (m === 'live') ? 'Déconnecter' : 'Connecter';
      updateButtonStates();
    }

    function updateButtonStates() {
      document.querySelectorAll('.twin-seat').forEach(b => {
        const id = b.textContent;
        const occ = mode === 'live' ? liveSeats.has(id) : (!!manualOccupancy[id] || !!actors[id]);
        b.classList.toggle('occ', occ);
      });
    }

    function isKnownSeat(id) {
      return typeof id === 'string' && seatById.has(id);
    }

    /* full state pushed by the Pi (source of truth) */
    function onServerState(seats) {
      const valid = (seats || []).filter(isKnownSeat);
      const set = new Set(valid);

      // drop stale entrance hints that never produced an occupancy change
      const now = performance.now();
      Object.keys(pendingDoorAt).forEach(id => {
        if (now - pendingDoorAt[id] > 15000) {
          delete pendingDoorAt[id];
          delete pendingDoor[id];
        }
      });

      valid.forEach(id => {
        // cancel a pending grace removal
        if (leaveTimers[id]) { clearTimeout(leaveTimers[id]); delete leaveTimers[id]; }
        if (!liveSeats.has(id)) {
          const a = actors[id];
          if (a && a.leaving) removeActor(id);
          const hint = pendingDoor[id] || null;
          delete pendingDoor[id];
          delete pendingDoorAt[id];
          spawnStudent(id, hint, { source: 'live' });
        }
        manualOccupancy[id] = true;
      });

      liveSeats.forEach(id => {
        if (!set.has(id)) {
          if (!leaveTimers[id]) {
            leaveTimers[id] = setTimeout(() => {
              delete leaveTimers[id];
              if (!liveSeats.has(id)) dismissStudent(id);
            }, CFG.graceMs);
          }
        }
      });

      SEATS.forEach(s => {
        if (!set.has(s.id)) manualOccupancy[s.id] = false;
      });

      liveSeats = set;
      updateButtonStates();
    }

    function handleSeatEvent(msg) {
      if (!msg || msg.type !== 'seat_event') return;
      const seat = msg.seat;
      if (!isKnownSeat(seat)) return;

      const eid = msg.event_id;
      if (eid !== undefined && eid !== null) {
        const key = String(eid);
        if (seenEventIds.has(key)) return;
        seenEventIds.add(key);
        if (seenEventIds.size > 400) {
          const first = seenEventIds.values().next().value;
          seenEventIds.delete(first);
        }
      }

      const action = msg.action || msg.event;
      if (action === 'enter') {
        pendingDoor[seat] = normalizeDoorId(msg.door);
        pendingDoorAt[seat] = performance.now();
      } else if (action === 'leave') {
        delete pendingDoor[seat];
        delete pendingDoorAt[seat];
        dismissStudent(seat);          // occupancy snapshot will follow
      }
    }

    function wsConnect() {
      if (!wsEnabled) return;
      const url = ($('twin-ws-url') && $('twin-ws-url').value.trim()) ||
                  localStorage.getItem(LS_URL) || 'ws://raspberrypi.local:8765';
      localStorage.setItem(LS_URL, url);
      try {
        setStatus('CONNECTING — ' + url, '#f1c40f');
        ws = new WebSocket(url);
      } catch (e) { scheduleReconnect(); return; }

      ws.onopen = () => {
        setStatus('LIVE', '#2ecc71');
        setMode('live');
      };
      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data);
          if (!msg || typeof msg !== 'object') return;
          if (msg.type === 'ping') return;
          if (msg.type === 'seat_event') { handleSeatEvent(msg); return; }
          if (Array.isArray(msg.seats)) onServerState(msg.seats);
        } catch (e) { console.warn('[Twin] bad message', ev.data); }
      };
      ws.onclose = () => {
        setStatus('TEST MODE — DISCONNECTED (Pi)', '#e74c3c');
        setMode('test');
        scheduleReconnect();
      };
      ws.onerror = () => { try { ws.close(); } catch (e) {} };
    }

    function scheduleReconnect() {
      if (!wsEnabled || reconnectTimer) return;
      reconnectTimer = setTimeout(() => { reconnectTimer = null; wsConnect(); }, 3000);
    }

    function wsToggle(on) {
      wsEnabled = on;
      localStorage.setItem('twinWsEnabled', on ? '1' : '0');
      if (on) wsConnect();
      else if (ws) { try { ws.close(); } catch (e) {} }
    }

    /* ================= TEST PANEL UI ================= */
    const css = `
      #twin-ui{position:fixed;bottom:16px;right:16px;z-index:20;font-family:Arial,sans-serif}
      #twin-panel{display:none;width:310px;max-height:74vh;overflow:auto;background:rgba(20,16,28,.92);
        border:1px solid #4a3d62;border-radius:12px;padding:14px;color:#eee;backdrop-filter:blur(8px)}
      #twin-panel.open{display:block}
      #twin-panel h3{margin:0 0 8px;font-size:14px;color:#c39bf3}
      #twin-toggle{background:#2b2238;color:#c39bf3;border:1px solid #4a3d62;border-radius:10px;
        padding:8px 14px;cursor:pointer;font-size:13px}
      .twin-row{display:flex;gap:8px;margin-bottom:10px}
      .twin-row input{flex:1;background:#181322;border:1px solid #4a3d62;color:#eee;border-radius:6px;
        padding:6px 8px;font-size:12px}
      .twin-row button, .twin-actions button, .twin-doors button{background:#3a2f4e;color:#eee;border:1px solid #5a4a78;
        border-radius:6px;padding:6px 10px;cursor:pointer;font-size:12px}
      .twin-actions{display:flex;gap:6px;margin:10px 0}
      .twin-doors{display:flex;gap:6px;align-items:center;margin:0 0 10px}
      .twin-doors span{font-size:11px;color:#8f83a8;flex:1}
      .twin-doors button.sel{background:#1e4fd6;border-color:#6f8cff;color:#fff}
      #twin-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:5px}
      .twin-seat{background:#241d33;border:1px solid #443a5e;color:#bbb;border-radius:5px;
        font-size:10px;padding:7px 2px;cursor:pointer}
      .twin-seat.occ{background:#1e4fd6;color:#fff;border-color:#6f8cff}
      .twin-seat:disabled{opacity:.35;cursor:not-allowed}
      #twin-status{font-size:11px;margin-left:8px}
      #twin-dot{display:inline-block;width:10px;height:10px;border-radius:50%;background:#e67e22;margin-left:14px}
      #twin-live-note{display:none;font-size:11px;color:#f1c40f}
      .twin-hint{font-size:10.5px;color:#8f83a8;margin-top:8px;line-height:1.5}
      .twin-legend{font-size:10px;color:#8f83a8;margin-top:4px}
    `;
    const styleEl = document.createElement('style');
    styleEl.textContent = css;
    document.head.appendChild(styleEl);

    const doorButtons = doorIds.map((id, i) => {
      const d = DOORS[id];
      const kind = d.kind === 'double' ? 'double' : 'simple';
      return `<button class="twin-door${i === 0 ? ' sel' : ''}" data-door="${id}">${d.name || id} · ${kind}</button>`;
    }).join('') +
      '<button class="twin-door" data-door="none" title="No entry animation (unknown door)">Aucune</button>';

    const ui = document.createElement('div');
    ui.id = 'twin-ui';
    ui.innerHTML = `
      <div id="twin-panel">
        <h3>🎫 Digital Twin — Salle 3D</h3>
        <div style="margin-bottom:10px">
          <span id="twin-dot"></span><span id="twin-status">TEST MODE</span>
        </div>
        <div class="twin-row">
          <input id="twin-ws-url" placeholder="ws://RPI_IP:8765">
          <button id="twin-ws-btn">Connecter</button>
        </div>
        <div class="twin-doors">
          <span>Entrée simulée :</span>
          ${doorButtons}
        </div>
        <div class="twin-actions">
          <button id="twin-fill">Tout remplir</button>
          <button id="twin-clear">Vider</button>
          <button id="twin-random">Aléatoire</button>
        </div>
        <div id="twin-grid"></div>
        <div class="twin-hint">
          <span id="twin-live-note">⚠️ Mode LIVE : producteur externe (YOLO à venir).</span><br>
          Mode TEST : clique sur une case pour simuler un étudiant. L'événement
          part vers le backend (2 s de présence → enregistrement PostgreSQL,
          3 s d'absence → clôture). Re-clique pour le faire sortir.<br>
          ID = C(colonne 1-4) - R(rangée 1-6). R1 = rangée la plus proche du tableau.
        </div>
        <div class="twin-legend">Animation : porte → couloir → chaise → position assise</div>
      </div>
      <button id="twin-toggle">🎫 Digital Twin</button>
    `;
    document.body.appendChild(ui);

    // seat buttons : R1 (front, nearest whiteboard) drawn at top
    const grid = $('twin-grid');
    for (let r = 1; r <= 6; r++) {
      for (let c = 1; c <= 4; c++) {
        const id = 'C' + c + '-R' + r;
        const b = document.createElement('button');
        b.className = 'twin-seat';
        b.textContent = id;
        b.title = 'Toggle ' + id;
        b.disabled = (mode === 'live');
        b.onclick = () => {
          if (mode === 'live') return;
          // TEST phase: the manual controls are the event producer.
          // Delegate to the backend client so the click becomes a real
          // PostgreSQL record instead of a local-only animation.
          if (window.TwinSimulation && window.TwinSimulation.isActive()) {
            window.TwinSimulation.toggleSeat(id);
            return;
          }
          if (manualOccupancy[id]) {
            manualOccupancy[id] = false;
            dismissStudent(id);
          } else if (!actors[id]) {
            manualOccupancy[id] = true;
            spawnStudent(id, selectedDoor);
          }
          updateButtonStates();
        };
        grid.appendChild(b);
      }
    }

    document.querySelectorAll('.twin-door').forEach(btn => {
      btn.onclick = () => {
        selectedDoor = btn.getAttribute('data-door');
        document.querySelectorAll('.twin-door').forEach(b => b.classList.toggle('sel', b === btn));
      };
    });

    $('twin-toggle').onclick = () => $('twin-panel').classList.toggle('open');

    $('twin-ws-btn').onclick = () => {
      wsToggle(!(wsEnabled && mode === 'live'));
    };

    $('twin-fill').onclick = () => {
      if (mode === 'live') return;
      if (window.TwinSimulation && window.TwinSimulation.isActive()) {
        window.TwinSimulation.fill();
        return;
      }
      SEATS.forEach((s, i) => {
        manualOccupancy[s.id] = true;
        if (!actors[s.id]) spawnStudent(s.id, selectedDoor, { delay: i * 70 });
      });
      updateButtonStates();
    };
    $('twin-clear').onclick = () => {
      if (mode === 'live') return;
      if (window.TwinSimulation && window.TwinSimulation.isActive()) {
        window.TwinSimulation.clear();
        return;
      }
      manualOccupancy = {};
      SEATS.forEach(s => dismissStudent(s.id));
      updateButtonStates();
    };
    $('twin-random').onclick = () => {
      if (mode === 'live') return;
      if (window.TwinSimulation && window.TwinSimulation.isActive()) {
        window.TwinSimulation.randomize();
        return;
      }
      manualOccupancy = {};
      SEATS.forEach((s, i) => {
        const occ = Math.random() < 0.4;
        manualOccupancy[s.id] = occ;
        if (occ) {
          if (!actors[s.id]) spawnStudent(s.id, selectedDoor, { delay: Math.random() * 500 });
        } else {
          dismissStudent(s.id);
        }
      });
      updateButtonStates();
    };

    if (localStorage.getItem(LS_URL)) $('twin-ws-url').value = localStorage.getItem(LS_URL);

    if (wsEnabled) wsConnect();

    // debugging / manual test API (no YOLO needed)
    window.ClassroomTwin = {
      spawnStudent: spawnStudent,
      dismissStudent: dismissStudent,
      setOccupied: setOccupied,
      applyState: applyState,
      setMode: setMode,
      getMode: () => mode,
      seats: SEATS.map(s => s.id),
      doors: doorIds,
      config: CC,
      actors: actors,
      state: () => Object.keys(actors).map(id => ({
        seat: id, state: actors[id].state,
        x: actors[id].avatar.position.x, z: actors[id].avatar.position.z
      }))
    };

    console.log('[Twin] ready —', SEATS.length, 'seats,', doorIds.length, 'doors');
  }

  // wait for scene + SEAT_DATA (robust against load order / caching)
  let tries = 0;
  const waitFor = setInterval(() => {
    tries++;
    const sc = window.__TW_SCENE;
    const sd = window.SEAT_DATA;
    if ((sc && sd && sd.length) || tries > 25) {
      clearInterval(waitFor);
      boot(sc, sd || []);
    }
  }, 200);
})();
