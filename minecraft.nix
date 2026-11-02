# Fabric 1.21.1; leave mods/ and config/ writable for SFTP/manual additions.
{ pkgs, lib, ... }:
{
  imports = [ ./minecraft-public-commands.nix ];

  nixpkgs.config.allowUnfreePredicate = pkg:
    lib.getName pkg == "minecraft-server";

  users.users.echo.extraGroups = [ "minecraft" ];

  services.minecraft-servers = {
    enable = true;
    eula = true;
    dataDir = "/srv/minecraft";
    # Expose only on the existing LAN/VPN interfaces, not every interface.
    openFirewall = false;
    managementSystem = {
      tmux.enable = false;
      systemd-socket.enable = true;
    };

    servers.fabric = {
      enable = true;
      autoStart = true;
      package = pkgs.fabricServers.fabric-1_21_1.override {
        jre_headless = pkgs.jdk21_headless;
      };
      jvmOpts = "-Xms1G -Xmx4G -XX:+UseG1GC";
      restart = "on-failure";
      # One cold backup when removing Disabilities, before starting without it.
      extraStartPre = ''
        stamp="backups/.before-removing-disabilities-20261021.done"
        if [[ -d world && ! -f "$stamp" ]]; then
          # UMask=0007 already gives this directory mode 0770. Do not set SGID:
          # RestrictSUIDSGID=true deliberately rejects it inside this service.
          mkdir -p backups
          archive="backups/before-removing-disabilities-$(date -u +%Y%m%dT%H%M%SZ).tar.gz"
          files=(world)
          for item in config whitelist.json ops.json banned-players.json banned-ips.json server.properties; do
            if [[ -e "$item" ]]; then
              files+=("$item")
            fi
          done
          ${pkgs.gnutar}/bin/tar --use-compress-program=${pkgs.gzip}/bin/gzip \
            -cf "$archive.partial" -- "''${files[@]}"
          ${pkgs.gzip}/bin/gzip -t "$archive.partial"
          mv "$archive.partial" "$archive"
          touch "$stamp"
          echo "Before-removing-Disabilities backup saved: $archive"
        fi
      '';
      serverProperties = {
        server-port = 25565;
        motd = "Echo - Fabric 1.21.1";
        gamemode = "survival";
        difficulty = "normal";
        max-players = 8;
        view-distance = 16;
        simulation-distance = 6;
        online-mode = true;
        # Only approved Minecraft accounts may join.
        white-list = true;
        enforce-whitelist = true;
        enable-rcon = false;
        enable-query = false;
        enable-command-block = false;
      };
      # Keep these files owned by Minecraft, not regenerated from Nix.
      # Empty attrsets mean unmanaged; existing whitelist.json and ops.json stay.
      # Administer with /whitelist add NAME, /whitelist remove NAME, /op NAME.
      whitelist = { };
      operators = { };
      # Manage only these individual jars, NOT the whole mods directory.
      # Change/remove these entries here rather than adding duplicate jar versions.
      symlinks = {
        "mods/krypton-0.2.8.jar" = pkgs.fetchurl {
          url = "https://cdn.modrinth.com/data/fQEb0iXm/versions/Acz3ttTp/krypton-0.2.8.jar";
          sha512 = "5f8cf96c79bfd4d893f1d70da582e62026bed36af49a7fa7b1e00fb6efb28d9ad6a1eec147020496b4fe38693d33fe6bfcd1eebbd93475612ee44290c2483784";
        };
        "mods/spark-1.10.109-fabric.jar" = pkgs.fetchurl {
          url = "https://cdn.modrinth.com/data/l6YH9Als/versions/cALUj9l1/spark-1.10.109-fabric.jar";
          sha512 = "367f574f6d28432067f09737577d799ced9c309c1725da1d09ffdfe10eacf461a66967205cc938131afbcc8b8255c8c25f8aa516e15f061c6481b6e7b8c94250";
        };
        "mods/fabric-api-0.116.17+1.21.1.jar" = pkgs.fetchurl {
          url = "https://cdn.modrinth.com/data/P7dR8mSH/versions/Mys3P7lK/fabric-api-0.116.17%2B1.21.1.jar";
          sha512 = "98c478217da19181f0e4df3ea6c2da6bdbda271c7544a4aea048513a49b444f1ff5b71dc0df6303017c48e8ff69b7bb6af6179bdf0d0e09ab3c1ee08411857a8";
        };
        "mods/lithium-fabric-0.15.4+mc1.21.1.jar" = pkgs.fetchurl {
          url = "https://cdn.modrinth.com/data/gvQqBUqZ/versions/N08Z8wog/lithium-fabric-0.15.4%2Bmc1.21.1.jar";
          sha512 = "182064b00e6315e2255b857eaab8eb759e6b042ebd4cc8b855ff0d93f875a5a7188fac49f878d7b29d4ef7e6b6341190ad7f6f6f39f4a6d2c62003468b08e4c6";
        };
        "mods/ferritecore-7.0.3-fabric.jar" = pkgs.fetchurl {
          url = "https://cdn.modrinth.com/data/uXXizFIs/versions/sOzRw3CG/ferritecore-7.0.3-fabric.jar";
          sha512 = "3ad31620fac4ff44327dc7dedbe162b2d978f3f246dc16255a6e400ce9592a0d326fe36a626f3c1bf30a11f813093cbb4dcc107af039cff724d0cdf648541fdf";
        };
      };
    };
  };

  networking.firewall.interfaces = {
    enp3s0.allowedTCPPorts = [ 25565 ];
    wg0.allowedTCPPorts = [ 25565 ];
  };

  systemd.tmpfiles.rules = [
    "d /srv/minecraft/fabric/mods 2770 minecraft minecraft - -"
    "d /srv/minecraft/fabric/config 2770 minecraft minecraft - -"
  ];

  systemd.services.minecraft-server-fabric.serviceConfig = {
    # Leave room for NixOS, Syncthing and Jellyfin on this 8 GB machine.
    MemoryMax = "5G";
    TimeoutStopSec = lib.mkForce "180s";
    ProtectSystem = "strict";
    ReadWritePaths = [ "/srv/minecraft/fabric" ];
  };
}
