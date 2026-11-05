// Optional bridge to a host app that shows this page in an iframe, such as
// agent-client. In a normal browser tab it does nothing.
//
// Protocol (version 1): messages are { v: 1, type, id, payload }. The page
// sends `hello`; if the host answers with `context` within 500 ms the bridge
// turns on, and attaching to an agent is handed to the host, which opens the
// agent's terminal itself. Otherwise agentgraph behaves as it always has.
(function () {
  const VERSION = '0.1.0';
  const state = { active: false, origin: null, context: null };
  let seq = 0;

  function post(type, payload) {
    const id = `ag${++seq}`;
    window.parent.postMessage({ v: 1, type, id, payload }, state.origin || '*');
    return id;
  }

  window.agentgraphBridge = {
    /** True once a host has answered hello. */
    get active() { return state.active; },
    /** The host's { machine, project, project_path }, or null. */
    get context() { return state.context; },
    /**
     * Hands an attach to the host. Returns false when there is no host, so
     * the caller falls back to its own behaviour.
     */
    tryAttach(agent) {
      if (!state.active) return false;
      post('attachAgent', agent);
      return true;
    },
  };

  if (window.parent === window) return;
  const deadline = Date.now() + 500;

  window.addEventListener('message', (e) => {
    if (e.source !== window.parent) return;
    // The first context fixes the host's origin; later messages must match it.
    if (state.origin && e.origin !== state.origin) return;
    const m = e.data;
    if (!m || m.v !== 1 || m.type !== 'context' || !m.payload) return;
    if (!state.origin) {
      if (Date.now() > deadline) return;
      state.origin = e.origin;
    }
    state.context = m.payload;
    state.active = true;
  });

  post('hello', { plugin: 'agentgraph', version: VERSION });
})();
