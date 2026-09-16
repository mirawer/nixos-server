#	/\	/\
#	\/	\/	/\
#	(	 )	\/	Home Server
#	)	(	(		~M

{
  config,
  lib,
  pkgs,
  ...
}:

{
  imports = [
    ./hardware-configuration.nix
    ./boot.nix
  ];

  networking.hostName = "server";
  time.timeZone = "Europe/Warsaw";

  i18n.defaultLocale = "en_US.UTF-8";
  console.keyMap = "pl";

  services.logind.settings.Login.HandleLidSwitch = "ignore";
  documentation.nixos.enable = false;

  environment.systemPackages = with pkgs; [
    vim
    wget
    git
    ghostty.terminfo
  ];

  system.stateVersion = "26.05";

  #  --  --  Security  --  --

  users.mutableUsers = false;

  users.users.echo = {
    isNormalUser = true;
    extraGroups = [
      "wheel"
      "media"
    ];
    hashedPasswordFile = "/var/lib/secrets/echo.pass";
    openssh.authorizedKeys.keys = [
      "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIJmN5fnFpjehHkQ2+iJhZvZy3gajCvex8iuFLdNP4ZX7 echo@server"
    ];
  };

  services.openssh = {
    enable = true;
    settings = {
      PasswordAuthentication = false;
      PermitRootLogin = "no";
      KbdInteractiveAuthentication = false;
      MaxAuthTries = 3;
    };
  };

  networking.firewall = {
    enable = true;
    allowedTCPPorts = [
      22
      8096
    ];
    allowedUDPPorts = [ 7359 ];
  };

  #  --  --  Optimalization  --  --

  networking.networkmanager.enable = false;

  zramSwap = {
    enable = true;
    algorithm = "zstd";
    memoryPercent = 50;
    priority = 100;
  };

  nix.gc = {
    automatic = true;
    dates = "weekly";
    options = "--delete-older-than 14d";
  };

  nix.optimise.automatic = true;

  boot.tmp.cleanOnBoot = true;

  services.journald.extraConfig = "SystemMaxUse=500M";

  #  --  --  Media  --  --

  users.groups.media = { };

  systemd.tmpfiles.rules = [
    #If it exists ten z if not then build with d

    "d /srv/media 0755 root root - -"
    "d /srv/media/films 2750 echo media - -"
    "d /srv/media/series 2750 echo media - -"

    "z /srv/media 0755 root root - -"
    "z /srv/media/films 2750 echo media - -"
    "z /srv/media/series 2750 echo media - -"
  ];

  #  --  --  Jellyfin  --  --
  services.jellyfin.enable = true;

  users.users.jellyfin.extraGroups = [ "media" ];

  # === Theme abyss ===
  systemd.services.jellyfin.preStart =
    let
      rev = "bbc69b560d0d362da8737b36821b69ce5c9f132f";
      abyssCss = pkgs.fetchurl {
        url =
          "https://raw.githubusercontent.com"
          + "/AumGupta/abyss-jellyfin/${rev}/abyss.css";
        hash =
          "sha256-KNRJM7Cy008iZGs3i0R3wT6DuVp+pywGukEUHQnfV6s=";
      };
      iconFont = pkgs.fetchurl {
        url =
          "https://cdn.jsdelivr.net/npm/material-icons@1.13.12"
          + "/iconfont/material-icons-round.woff2";
        hash =
          "sha256-yUjxJjNBaZs8HpxV2NDz5EZmnQ8rnVVJTGFpIiwCQ6Y=";
      };
      cfgDir = config.services.jellyfin.configDir;
    in
    ''
      set -o pipefail

      mkdir -p ${cfgDir}
      {
        printf '<?xml version="1.0" encoding="utf-8"?>\n'
        printf '<BrandingOptions>\n<CustomCss>'

        sed -e '/fonts.googleapis.com/d' \
            -e '/cdn.jsdelivr.net/d' \
            ${abyssCss} \
          | sed -e 's/&/\&amp;/g' \
                -e 's/</\&lt;/g'

        printf "@font-face{"
        printf "font-family:'Material Icons Round';"
        printf "font-style:normal;font-weight:400;"
        printf "src:url(data:font/woff2;base64,"
        base64 -w0 ${iconFont}
        printf ") format('woff2');}"

        printf '</CustomCss>\n</BrandingOptions>\n'
      } > ${cfgDir}/branding.xml

      test "$(wc -c < ${cfgDir}/branding.xml)" -gt 200000
    '';

}
