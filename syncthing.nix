{ config, lib, pkgs, ... }:
let
  cfg = config.services.syncthing;
  ignoreFile = pkgs.writeText "obsidian-vault-stignore" ''
    // Local UI layouts and technical work files are not shared.
    /.obsidian/workspace*.json
    /PI/.*-work.*
    /.git
    .DS_Store
    Thumbs.db
  '';
in
{
  services.syncthing = {
    enable = true;
    # Native dedicated account, separate from Hermes and Gmail credentials.
    dataDir = "/var/lib/syncthing";
    configDir = "/var/lib/syncthing/.config/syncthing";
    # Kernel permissions also exclude the local Hermes agent from this API.
    guiAddress = "unix:///run/syncthing/gui.sock";
    openDefaultPorts = true;
    overrideDevices = false;
    overrideFolders = false;
    settings = {
      gui.unixSocketPermissions = "0660";
      options = {
        localAnnounceEnabled = true;
        globalAnnounceEnabled = true;
        relaysEnabled = true;
        natEnabled = false;
        urAccepted = -1;
      };
    };
  };

  # echo can edit the shared copy and forward its admin socket over SSH.
  # Hermes is deliberately NOT added to this group.
  users.users.echo.extraGroups = [ "syncthing" ];
  users.users.syncthing.homeMode = "750";
  systemd.tmpfiles.rules = [
    "d /var/lib/syncthing 0750 syncthing syncthing - -"
    "d /var/lib/syncthing/.config 0700 syncthing syncthing - -"
    "d /var/lib/syncthing/.config/syncthing 0700 syncthing syncthing - -"
    "d \"/var/lib/syncthing/Obsidian Vault\" 2770 syncthing syncthing - -"
    # C only initializes a missing ignore file; it preserves later GUI edits.
    "C \"/var/lib/syncthing/Obsidian Vault/.stignore\" 0660 syncthing syncthing - ${ignoreFile}"
  ];

  systemd.services.syncthing.serviceConfig = {
    # Offline initialization keeps credentials runtime-only and preserves the cert.
    ExecStartPre = pkgs.writeShellScript "syncthing-prestart" ''
      umask 0077
      ${lib.getExe cfg.package} generate --config ${lib.escapeShellArg cfg.configDir} --data ${lib.escapeShellArg cfg.databaseDir} >/dev/null
      ${pkgs.xmlstarlet}/bin/xmlstarlet ed -L -u /configuration/options/natEnabled -v false ${lib.escapeShellArg "${cfg.configDir}/config.xml"}
    '';
    RuntimeDirectoryMode = "0750";
    UMask = "0007";
    ProtectHome = true;
    ProtectSystem = "strict";
    ReadWritePaths = [ "/var/lib/syncthing" "/run/syncthing" ];
  };
  systemd.services.syncthing-init.serviceConfig = {
    Group = "syncthing";
    RuntimeDirectoryMode = "0700";
    UMask = "0077";
    ProtectHome = true;
    ProtectSystem = "strict";
    NoNewPrivileges = true;
    PrivateTmp = true;
    ReadWritePaths = [ "/var/lib/syncthing" "/run/syncthing-init" ];
  };
}
