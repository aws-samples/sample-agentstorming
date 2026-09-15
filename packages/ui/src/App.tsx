// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import { useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  claim,
  claimModerator,
  fetchSnapshot,
  listPublicRooms,
  loadSession,
  openStream,
  postMessage,
  raiseHand,
  saveSession,
  Session,
} from "./client";

const MODERATOR_AFFILIATIONS = new Set(["room-owner", "original-moderator"]);

type PublicRoom = {
  room_id: string;
  title: string;
  description: string;
  visibility: string;
  state: string;
  participant_count: number | null;
};

type LinkTarget = { roomId: string; token: string } | null;

function parseInviteLink(): LinkTarget {
  // Accept three URL shapes:
  //   /r/<room>/join?t=<token>            (search query)
  //   /#/r/<room>/join?t=<token>          (hash routing)
  //   #r/<room>/join?t=<token>            (legacy)
  const here =
    window.location.pathname + window.location.search + window.location.hash;
  const m = here.match(/(?:^|\/|#\/?)r\/([^/?#]+)\/join\?(?:.*&)?t=([A-Za-z0-9_-]+)/);
  if (!m) return null;
  return { roomId: decodeURIComponent(m[1]), token: m[2] };
}

function clearInviteFromUrl() {
  // Drop the ?t= query so the token never lingers in browser history.
  const hasToken =
    window.location.search.includes("t=") || window.location.hash.includes("t=");
  if (!hasToken) return;
  const cleanPath = window.location.pathname.replace(/\/r\/[^/]+\/join$/, "");
  window.history.replaceState({}, "", cleanPath || "/");
}

function shortPid(pid?: string | null) {
  if (!pid) return "(unknown)";
  return pid.split("@")[0].slice(0, 10);
}

export function App() {
  const [baseUrl, setBaseUrl] = useState<string>(localStorage.getItem("agentstorming.baseUrl") || "");
  const [roomId, setRoomId] = useState<string>(localStorage.getItem("agentstorming.roomId") || "");
  const [inviteToken, setInviteToken] = useState<string>("");
  const [session, setSession] = useState<Session | null>(null);
  const [events, setEvents] = useState<any[]>([]);
  const [state, setState] = useState<any>({});
  const [composer, setComposer] = useState("");
  const [status, setStatus] = useState("disconnected");
  const [errors, setErrors] = useState<{ id: number; msg: string }[]>([]);
  const [rooms, setRooms] = useState<PublicRoom[]>([]);
  const [showRooms, setShowRooms] = useState(false);
  const [autoJoinPhase, setAutoJoinPhase] = useState<"idle" | "joining" | "done">("idle");
  const closeRef = useRef<(() => void) | null>(null);
  const errIdRef = useRef(0);
  // Tracks the seq of the last metadata_snapshot we applied. Older
  // snapshots replayed from the event log (e.g. seq=0 created at room
  // birth before any participant joined) are ignored so they can't
  // clobber the live snapshot delivered as id="snapshot" on connect.
  const lastSnapshotSeqRef = useRef<number>(-Infinity);

  function pushError(msg: string) {
    const id = ++errIdRef.current;
    setErrors((prev) => [...prev, { id, msg }]);
  }
  function dismissError(id: number) {
    setErrors((prev) => prev.filter((e) => e.id !== id));
  }
  function clearErrors() {
    setErrors([]);
  }

  // Boot: config.json + invite-link detection.
  useEffect(() => {
    (async () => {
      try {
        const r = await fetch("/config.json", { cache: "no-store" });
        if (r.ok) {
          const cfg = await r.json();
          if (cfg.api_base && !localStorage.getItem("agentstorming.baseUrl")) {
            setBaseUrl(cfg.api_base);
          }
          if (cfg.default_room && !localStorage.getItem("agentstorming.roomId")) {
            setRoomId(cfg.default_room);
          }
        }
      } catch {
        /* optional */
      }
      const link = parseInviteLink();
      if (link) {
        setRoomId(link.roomId);
        setInviteToken(link.token);
        // Strip the token from the URL immediately so it never lingers
        // in browser history regardless of which path consumes it.
        clearInviteFromUrl();
      }
    })();
  }, []);

  // Resume existing session when baseUrl+roomId known.
  useEffect(() => {
    if (!baseUrl || !roomId) return;
    const s = loadSession(baseUrl, roomId);
    if (s.accessToken && s.pid) {
      setSession(s);
      setStatus("connected");
    }
  }, [baseUrl, roomId]);

  // Fetch public-room list when baseUrl known.
  useEffect(() => {
    if (!baseUrl) return;
    (async () => {
      try {
        const list = await listPublicRooms(baseUrl);
        setRooms(list);
      } catch {
        /* leave empty */
      }
    })();
  }, [baseUrl]);

  // Auto-claim when an invite token came in via the URL and we're not
  // already in a session. This is the seamless flow: paste the link,
  // we boot, we redeem, we drop the user straight into the room.
  useEffect(() => {
    if (session) return;
    if (autoJoinPhase !== "idle") return;
    if (!baseUrl || !roomId || !inviteToken) return;
    let cancelled = false;
    (async () => {
      setAutoJoinPhase("joining");
      try {
        localStorage.setItem("agentstorming.baseUrl", baseUrl);
        localStorage.setItem("agentstorming.roomId", roomId);
        const s = loadSession(baseUrl, roomId);
        const snap = await claim(s, inviteToken);
        if (cancelled) return;
        setSession(s);
        setState(snap?.payload || {});
        setStatus("connected");
        setInviteToken("");
        clearInviteFromUrl();
      } catch (e: any) {
        if (cancelled) return;
        pushError(`Auto-join failed: ${String(e)}`);
      } finally {
        if (!cancelled) setAutoJoinPhase("done");
      }
    })();
    return () => {
      cancelled = true;
    };
    // NB: autoJoinPhase is intentionally NOT in the dep array.
    // setAutoJoinPhase("joining") below would otherwise re-trigger this
    // effect, cancel the in-flight claim, and leave the UI stuck on
    // "Joining…". The internal phase guard handles the same job.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [baseUrl, roomId, inviteToken, session]);

  // SSE.
  useEffect(() => {
    if (!session) return;
    closeRef.current?.();
    setEvents([]);
    lastSnapshotSeqRef.current = -Infinity;
    // Always pull a fresh REST snapshot first. The SSE replay can include
    // older persisted snapshots (e.g. seq=0 from room creation) which
    // would clobber sidebar state — pulling REST first guarantees the
    // sidebar matches what the server thinks is current. We bump
    // lastSnapshotSeqRef to +Infinity so any older replayed snapshot is
    // ignored; live (newer) snapshots are still accepted via the
    // periodic ticker once they appear.
    fetchSnapshot(session)
      .then((env) => {
        if (env?.payload) {
          setState(env.payload);
          lastSnapshotSeqRef.current = Number.POSITIVE_INFINITY;
        }
      })
      .catch((e) => pushError(`Snapshot fetch failed: ${String(e)}`));
    const close = openStream(session, {
      onOpen: () => setStatus("connected"),
      onEvent: (e) => {
        setEvents((prev) => [...prev, e]);
        if (e.type === "org.agentstorming.metadata_snapshot") {
          // The server's synthesized fresh snapshot has no integer seq
          // (id="snapshot"). Treat that as monotonically newest. For
          // replayed snapshots, only apply if seq is strictly greater
          // than what we've already applied — old persisted snapshots
          // (seq=0 from room creation) must not clobber live state.
          const seq = typeof e.seq === "number" ? e.seq : Number.POSITIVE_INFINITY;
          if (seq >= lastSnapshotSeqRef.current) {
            lastSnapshotSeqRef.current = seq;
            setState(e.payload || {});
          }
        }
        saveSession(session);
      },
      onError: (e) => {
        pushError(`Stream error: ${String(e)}`);
        setStatus("reconnecting");
      },
    });
    closeRef.current = close;
    return () => {
      closeRef.current?.();
      closeRef.current = null;
    };
  }, [session]);

  async function doJoin() {
    clearErrors();
    localStorage.setItem("agentstorming.baseUrl", baseUrl);
    localStorage.setItem("agentstorming.roomId", roomId);
    const s = loadSession(baseUrl, roomId);
    try {
      const snap = await claim(s, inviteToken);
      setSession(s);
      setState(snap?.payload || {});
      setStatus("connected");
      setInviteToken("");
    } catch (e: any) {
      pushError(`Join failed: ${String(e)}`);
    }
  }

  async function doSend() {
    if (!session || !composer.trim()) return;
    try {
      await postMessage(session, composer);
      setComposer("");
    } catch (e: any) {
      pushError(`Send failed: ${String(e)}`);
    }
  }

  async function doClaimModerator() {
    if (!session) return;
    try {
      await claimModerator(session);
    } catch (e: any) {
      pushError(`Claim moderator failed: ${String(e)}`);
    }
  }

  async function doRaiseHand() {
    if (!session) return;
    try {
      await raiseHand(session);
    } catch (e: any) {
      pushError(`Raise-hand failed: ${String(e)}`);
    }
  }

  function switchToRoom(pr: PublicRoom) {
    setRoomId(pr.room_id);
    setInviteToken("");
    setSession(null);
    setStatus("disconnected");
    setShowRooms(false);
    setAutoJoinPhase("idle");
  }

  function leave() {
    localStorage.removeItem("agentstorming.session:" + roomId);
    setSession(null);
    setStatus("disconnected");
    setEvents([]);
    setState({});
    setInviteToken("");
    setAutoJoinPhase("idle");
  }

  // Derived state.
  const cfg = state.config || {};
  const raiseHandRequired: boolean = !!cfg.raise_hand_required;
  const myPid = session?.pid || "";
  const myParticipant = useMemo(
    () => (state.participants || []).find((p: any) => p.pid === myPid),
    [state.participants, myPid],
  );
  const myAffiliation = myParticipant?.affiliation || session?.affiliation || "(unknown)";
  const canClaimModerator = MODERATOR_AFFILIATIONS.has(myAffiliation);
  const imModerator = state.moderator_pid && state.moderator_pid === myPid;
  const myActiveGrant =
    state.active_grant && state.active_grant.pid === myPid ? state.active_grant : null;
  const myRaisedHand = (state.raised_hands || []).find(
    (h: any) => h.pid === myPid && h.state !== "GRANTED",
  );
  const moderatorBypass = imModerator || myAffiliation === "room-owner";
  const canPostFreely = !raiseHandRequired || moderatorBypass || !!myActiveGrant;

  // Login screen — only shown when no auto-join is in progress.
  if (!session) {
    const showLoginForm = autoJoinPhase !== "joining";
    return (
      <div className="layout">
        <main className="login">
          <h1>Agent Storming</h1>
          {autoJoinPhase === "joining" && (
            <p className="hint">
              Joining room <code>{roomId}</code>…
            </p>
          )}
          {showLoginForm && (
            <>
              <p className="hint">
                Paste an invite link or fill the fields manually. The server decides
                your role from the invite — owner, moderator, or participant.
              </p>
              <label>
                <span className="label">Server URL</span>
                <input
                  placeholder="http://127.0.0.1:8440"
                  value={baseUrl}
                  onChange={(e) => setBaseUrl(e.target.value)}
                />
              </label>
              <label>
                <span className="label">Room ID</span>
                <input
                  placeholder="my-room"
                  value={roomId}
                  onChange={(e) => setRoomId(e.target.value)}
                />
              </label>
              <label>
                <span className="label">Invite token</span>
                <input
                  placeholder="paste token (or use a /r/<room>/join?t=… link)"
                  value={inviteToken}
                  onChange={(e) => setInviteToken(e.target.value)}
                />
              </label>
              <button className="primary" onClick={doJoin}>
                Join room
              </button>
            </>
          )}
          <ErrorList errors={errors} dismiss={dismissError} />
          {rooms.length > 0 && showLoginForm && (
            <section className="public-rooms">
              <h3>Public rooms on this server</h3>
              <ul>
                {rooms.map((r) => (
                  <li key={r.room_id}>
                    <button
                      className="room-link"
                      onClick={() => {
                        setRoomId(r.room_id);
                        setInviteToken("");
                      }}
                    >
                      <strong>{r.title || r.room_id}</strong>
                      <span className="small"> · {r.participant_count ?? "?"} member(s)</span>
                      <div className="desc">{r.description}</div>
                    </button>
                  </li>
                ))}
              </ul>
              <p className="hint small">
                Select a public room, then paste an invite token.
              </p>
            </section>
          )}
        </main>
      </div>
    );
  }

  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="sidebar-header">
          <h1>Agent Storming</h1>
          <span className={`status status-${status}`}>{status}</span>
        </div>

        <section className="me">
          <div className="me-row">
            <span className="label-small">Room</span>
            <span className="value">{roomId}</span>
          </div>
          <div className="me-row">
            <span className="label-small">You</span>
            <span className="value">{shortPid(myPid)}</span>
          </div>
          <div className="me-row">
            <span className="label-small">Role</span>
            <span className={`value affiliation aff-${myAffiliation}`}>
              {myAffiliation}
              {myParticipant?.deputy_rank ? ` #${myParticipant.deputy_rank}` : ""}
            </span>
          </div>
          {raiseHandRequired && (
            <div className="me-row">
              <span className="label-small">Mode</span>
              <span className="value mode-pill">raise-hand-required</span>
            </div>
          )}
        </section>

        <div className="actions">
          {canClaimModerator && !imModerator && (
            <button className="btn btn-primary" onClick={doClaimModerator}>
              Take moderator seat
            </button>
          )}
          <button className="btn btn-ghost" onClick={() => setShowRooms((v) => !v)}>
            {showRooms ? "Hide rooms" : "Switch room"}
          </button>
          <button className="btn btn-quiet" onClick={leave}>
            Leave / sign out
          </button>
        </div>

        {showRooms && (
          <section className="public-rooms">
            <h3>Public rooms</h3>
            {rooms.length === 0 && <p className="hint small">No public rooms.</p>}
            <ul>
              {rooms.map((r) => (
                <li key={r.room_id}>
                  <button className="room-link" onClick={() => switchToRoom(r)}>
                    <strong>{r.title || r.room_id}</strong>
                    <span className="small"> · {r.participant_count ?? "?"}</span>
                  </button>
                </li>
              ))}
            </ul>
            <p className="hint small">Each room needs its own invite.</p>
          </section>
        )}

        <h3>Participants ({(state.participants || []).length})</h3>
        <ul className="participants">
          {(state.participants || []).map((p: any) => {
            const isMod = p.pid === state.moderator_pid;
            const isMe = p.pid === myPid;
            return (
              <li
                key={p.pid}
                className={[
                  isMod ? "mod" : "",
                  isMe ? "me-row-li" : "",
                ].join(" ").trim()}
              >
                <span className="dot" />
                <span className="who">
                  {shortPid(p.pid)}
                  {isMe ? " (you)" : ""}
                </span>
                <span className="aff small">
                  {p.affiliation}
                  {p.deputy_rank ? ` #${p.deputy_rank}` : ""}
                </span>
              </li>
            );
          })}
        </ul>

        {raiseHandRequired && (
          <>
            <h3>Raised hands ({(state.raised_hands || []).length})</h3>
            <ul className="hands">
              {(state.raised_hands || []).length === 0 && (
                <li className="hint small">(none)</li>
              )}
              {(state.raised_hands || []).map((h: any) => (
                <li key={h.hand_id}>
                  ✋ {shortPid(h.pid)}{" "}
                  <span className="small">{h.state.toLowerCase()}</span>
                </li>
              ))}
            </ul>
          </>
        )}

        {state.summary && (
          <>
            <h3>Summary</h3>
            <div className="summary">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{state.summary}</ReactMarkdown>
            </div>
          </>
        )}

        {Array.isArray(state.documents) && state.documents.length > 0 && (
          <>
            <h3>Documents</h3>
            <ul className="docs">
              {state.documents.map((d: any) => (
                <li key={d.id}>
                  <strong>{d.title}</strong>
                  <span className="small"> · {d.type}</span>
                </li>
              ))}
            </ul>
          </>
        )}
      </aside>

      <main className="log">
        <header className="room-banner">
          <div>
            <h2>{state.room_id || roomId}</h2>
            {state.room_state && (
              <span className={`pill state-${state.room_state.toLowerCase()}`}>
                {state.room_state}
              </span>
            )}
          </div>
          <div className="banner-meta">
            {raiseHandRequired ? (
              <span className="pill mode">raise-hand-required</span>
            ) : (
              <span className="pill mode-free">free-speak</span>
            )}
            <span className="pill quiet">{(state.participants || []).length} participants</span>
          </div>
        </header>

        <div className="messages">
          {events
            .filter(
              (e) =>
                e.type === "org.agentstorming.message" ||
                e.type === "org.agentstorming.attachment",
            )
            .map((e, i) => {
              const mine = e.sender === myPid;
              const isMod = e.sender === state.moderator_pid;
              return (
                <article
                  key={e.id || i}
                  className={`msg ${mine ? "msg-mine" : ""} ${isMod ? "msg-mod" : ""}`}
                >
                  <header>
                    <span className="speaker">
                      {shortPid(e.sender)}
                      {isMod ? " · moderator" : ""}
                    </span>
                    <span className="ts">{e.ts_server || e.ts_sender}</span>
                    <span className="seq">#{e.seq}</span>
                  </header>
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>
                    {e.payload?.text || ""}
                  </ReactMarkdown>
                </article>
              );
            })}
        </div>

        <ErrorList errors={errors} dismiss={dismissError} />

        <div className={`composer ${!canPostFreely ? "composer-gated" : ""}`}>
          {raiseHandRequired && (
            <div className="composer-status">
              {moderatorBypass ? (
                <span className="hint">moderator/owner — you bypass raise-hand.</span>
              ) : myActiveGrant ? (
                <span className="hint ok">
                  ✓ you hold the floor (grant {myActiveGrant.grant_id.slice(0, 8)}…)
                </span>
              ) : myRaisedHand ? (
                <span className="hint">
                  ✋ hand raised. waiting for the moderator to grant.
                </span>
              ) : (
                <span className="hint warn">
                  raise-hand mode: click <strong>Raise hand</strong> first.
                </span>
              )}
            </div>
          )}
          <div className="composer-row">
            {raiseHandRequired && !moderatorBypass && (
              <button
                className="btn btn-ghost"
                onClick={doRaiseHand}
                disabled={!!myRaisedHand || !!myActiveGrant}
                title={
                  myActiveGrant
                    ? "you already have a grant"
                    : myRaisedHand
                      ? "your hand is already raised"
                      : "raise hand"
                }
              >
                ✋ Raise hand
              </button>
            )}
            <textarea
              value={composer}
              onChange={(e) => setComposer(e.target.value)}
              onKeyDown={(e) => {
                if ((e.metaKey || e.ctrlKey) && e.key === "Enter") doSend();
              }}
              placeholder={
                canPostFreely
                  ? "Speak…  (⌘/Ctrl+Enter to send)"
                  : "Raise your hand first"
              }
              disabled={!canPostFreely}
            />
            <button
              className="btn btn-primary"
              onClick={doSend}
              disabled={!canPostFreely || !composer.trim()}
            >
              Send
            </button>
          </div>
        </div>
      </main>
    </div>
  );
}

function ErrorList({
  errors,
  dismiss,
}: {
  errors: { id: number; msg: string }[];
  dismiss: (id: number) => void;
}) {
  if (!errors.length) return null;
  return (
    <ul className="errors">
      {errors.map((e) => (
        <li key={e.id}>
          <span>{e.msg}</span>
          <button onClick={() => dismiss(e.id)} aria-label="dismiss">
            ×
          </button>
        </li>
      ))}
    </ul>
  );
}
