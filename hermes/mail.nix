{ pkgs, config, ... }:
let
  stateDir = "/var/lib/hermes-mail";
  briefStateDir = "/var/lib/hermes/mail-brief";
  reader = pkgs.writeText "hermes-mail-read.py" (builtins.readFile ./mail-read.py);
  brief = pkgs.writeText "hermes-mail-brief.py" (builtins.readFile ./mail-brief.py);
  readerPython = pkgs.python3.withPackages (ps: [ ps.pymupdf ps.pillow ps.openpyxl ]);
  ocr = pkgs.tesseract.override { enableLanguages = [ "eng" "pol" ]; };
  client = pkgs.writeShellScriptBin "hermes-mail" ''
    exec ${pkgs.python3}/bin/python3 -B ${reader} "$@"
  '';
  briefClient = pkgs.writeShellScriptBin "hermes-mail-brief" ''
    exec ${pkgs.python3}/bin/python3 -B ${brief} \
      --reader ${reader} \
      --hermes ${config.services.hermes-agent.package}/bin/hermes "$@"
  '';
in
{
  # Runtime-only account configuration/passwords; never embedded in the store.
  users.groups.hermes-mail = { };
  users.users.hermes-mail = {
    isSystemUser = true;
    group = "hermes-mail";
    home = stateDir;
    createHome = false;
  };

  environment.systemPackages = [ pkgs.himalaya client briefClient ];
  services.hermes-agent.extraPackages = [ client ];

  systemd.tmpfiles.rules = [
    "d ${stateDir} 0700 hermes-mail hermes-mail -"
    "d ${stateDir}/.config 0700 hermes-mail hermes-mail -"
    "d ${stateDir}/.config/himalaya 0700 hermes-mail hermes-mail -"
    "d ${stateDir}/.cache 0700 hermes-mail hermes-mail -"
    "d ${stateDir}/secrets 0700 hermes-mail hermes-mail -"
    "d ${stateDir}/trash-plans 0700 hermes-mail hermes-mail -"
    "d ${briefStateDir} 0700 hermes hermes -"
  ];

  # Read-only reads + separately OS-human-authorized UID MOVE to Gmail Trash.
  # Existing socket/credential isolation stays; no new agent or polling service.
  systemd.sockets.hermes-mail-read = {
    description = "INBOX broker with separately approved Gmail Trash plans";
    wantedBy = [ "sockets.target" ];
    socketConfig = {
      ListenStream = "/run/hermes-mail-read.sock";
      SocketUser = "root";
      SocketGroup = "hermes";
      SocketMode = "0660";
      Accept = true;
      MaxConnections = 2;
      RemoveOnStop = true;
    };
  };

  systemd.services."hermes-mail-read@" = {
    description = "Private mail worker (read-only reads; approved Gmail UID MOVE only)";
    wants = [ "network-online.target" ];
    after = [ "network-online.target" ];
    path = [ pkgs.coreutils pkgs.bash ];
    environment = {
      HOME = stateDir;
      XDG_CONFIG_HOME = "${stateDir}/.config";
      XDG_CACHE_HOME = "${stateDir}/.cache";
      SSL_CERT_FILE = "/etc/ssl/certs/ca-certificates.crt";
      NIX_SSL_CERT_FILE = "/etc/ssl/certs/ca-certificates.crt";
      HERMES_MAIL_TESSERACT = "${ocr}/bin/tesseract";
      OMP_THREAD_LIMIT = "1";
    };
    serviceConfig = {
      Type = "exec";
      User = "hermes-mail";
      Group = "hermes-mail";
      ExecStart = "${readerPython}/bin/python3 -B ${reader} --serve --himalaya ${pkgs.himalaya}/bin/himalaya --config ${stateDir}/.config/himalaya/config.toml";
      StandardInput = "socket";
      StandardOutput = "inherit";
      StandardError = "journal";
      WorkingDirectory = stateDir;
      StateDirectory = "hermes-mail";
      StateDirectoryMode = "0700";
      UMask = "0077";
      RuntimeMaxSec = "120s";
      TimeoutStopSec = "5s";
      KillMode = "control-group";
      MemoryMax = "768M";
      TasksMax = 64;
      NoNewPrivileges = true;
      PrivateTmp = true;
      PrivateDevices = true;
      ProtectSystem = "strict";
      ProtectHome = true;
      ReadOnlyPaths = [ "${stateDir}/.config" "${stateDir}/secrets" ];
      NoExecPaths = [ stateDir "/tmp" "/var/tmp" ];
      ProtectKernelTunables = true;
      ProtectKernelModules = true;
      ProtectControlGroups = true;
      RestrictSUIDSGID = true;
      RestrictAddressFamilies = [ "AF_UNIX" "AF_INET" "AF_INET6" ];
      CapabilityBoundingSet = "";
    };
  };

  systemd.services.hermes-mail-brief = {
    description = "Daily Gmail brief to the approved Discord forum";
    wants = [ "network-online.target" "hermes-mail-read.socket" ];
    after = [ "network-online.target" "hermes-mail-read.socket" ];
    environment = {
      HOME = "/var/lib/hermes";
      HERMES_HOME = "/var/lib/hermes/.hermes";
      HERMES_MANAGED = "true";
      HERMES_TUI = "0";
      PYTHONTZPATH = "${pkgs.tzdata}/share/zoneinfo";
      SSL_CERT_FILE = "/etc/ssl/certs/ca-certificates.crt";
      NIX_SSL_CERT_FILE = "/etc/ssl/certs/ca-certificates.crt";
    };
    path = [ pkgs.coreutils ];
    serviceConfig = {
      Type = "oneshot";
      User = "hermes";
      Group = "hermes";
      EnvironmentFile = [ "/home/echo/.config/hermes/discord.env" ];
      ExecStart = "${briefClient}/bin/hermes-mail-brief";
      WorkingDirectory = briefStateDir;
      StateDirectory = "hermes/mail-brief";
      StateDirectoryMode = "0700";
      UMask = "0077";
      TimeoutStartSec = "25min";
      TimeoutStopSec = "10s";
      KillMode = "control-group";
      MemoryMax = "1536M";
      TasksMax = 128;
      NoNewPrivileges = true;
      PrivateTmp = true;
      PrivateDevices = true;
      ProtectSystem = "strict";
      ProtectHome = true;
      ReadWritePaths = [ "/var/lib/hermes/.hermes" briefStateDir ];
      ReadOnlyPaths = [ "/var/lib/hermes/workspace" ];
      NoExecPaths = [ briefStateDir "/tmp" "/var/tmp" ];
      ProtectKernelTunables = true;
      ProtectKernelModules = true;
      ProtectControlGroups = true;
      RestrictSUIDSGID = true;
      RestrictAddressFamilies = [ "AF_UNIX" "AF_INET" "AF_INET6" ];
      CapabilityBoundingSet = "";
    };
  };

  systemd.timers.hermes-mail-brief = {
    description = "Gmail brief at 08:00 Europe/Warsaw (including DST)";
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnCalendar = "*-*-* 08:00:00 Europe/Warsaw";
      AccuracySec = "1s";
      RandomizedDelaySec = 0;
      Persistent = true;
      Unit = "hermes-mail-brief.service";
    };
  };
}
