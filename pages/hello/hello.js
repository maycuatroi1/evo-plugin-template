// The page of lumen-hello. It reaches the hub only through window.evo (the hub's UI kit 1 and the bridge
// evo-plugin/1). The waves are records of the hub, collection "waves" of this plugin in this project (permission
// records:use): evo.call("records.list" | "records.put") reads and writes them with the viewer's session, so the hub
// shows each viewer only the waves their grant and the wave's audience reach. evo.route calls the plugin's own route,
// /v1/x/lumen-hello/<project>/waves, once, for the greeting and for the level and audience the admin set.
// Its words come from i18n/<locale>.js beside this page, which the kit loads; the kit keeps the theme and the frame's
// height.
(function () {
  "use strict";
  var evo = window.evo;
  var COLLECTION = "waves";
  var LIMIT = 20;
  var KEY_BASE = 1e13;
  var settings = { level: "", audience: "everyone" };

  // Keys count down from KEY_BASE, so the newest wave sorts first in a list.
  function newKey() {
    var random = Math.random().toString(16).slice(2, 10).padEnd(8, "0");
    return String(KEY_BASE - Date.now()).padStart(13, "0") + "-" + random;
  }

  function show(records) {
    var body = document.querySelector("#waves tbody");
    body.textContent = "";
    records.forEach(function (record) {
      var wave = record.body || {};
      var row = document.createElement("tr");
      var name = document.createElement("td");
      name.textContent = wave.name || "";
      var when = document.createElement("td");
      when.className = "evo-hide-sm";
      when.textContent = wave.at ? new Date(wave.at).toLocaleString(document.documentElement.lang || "en") : "";
      row.appendChild(name);
      row.appendChild(when);
      body.appendChild(row);
    });
    document.getElementById("empty").hidden = records.length > 0;
  }

  function load() {
    return evo.call("records.list", { collection: COLLECTION, limit: LIMIT }).then(function (page) {
      show(page.records || []);
    });
  }

  function failed(error) {
    evo.toast(evo.t("failed", { message: (error && error.message) || String(error) }), "error");
  }

  evo.ready
    .then(function (context) {
      document.querySelectorAll("[data-i18n]").forEach(function (node) {
        node.textContent = evo.t(node.getAttribute("data-i18n"));
      });
      var input = document.getElementById("name");
      input.placeholder = evo.t("name");
      input.value = (context.viewer && context.viewer.login) || "";
      return evo.route("waves");
    })
    .then(function (answer) {
      var board = answer.body;
      settings = { level: board.level || "", audience: board.audience || "everyone" };
      document.getElementById("greeting").textContent = evo.t("greeting", { greeting: board.greeting });
      return load();
    })
    .catch(failed);

  document.getElementById("wave").addEventListener("submit", function (event) {
    event.preventDefault();
    var name = document.getElementById("name").value.trim();
    if (!name) return;
    var record = {
      collection: COLLECTION,
      key: newKey(),
      body: { name: name, at: new Date().toISOString() },
      audience: settings.audience,
      revision: 0,
    };
    if (settings.level) record.level = settings.level;
    evo
      .call("records.put", record)
      .then(load)
      .then(function () {
        evo.toast(evo.t("sent"), "success");
      })
      .catch(failed);
  });
})();
