{ ... }:

{
  # Usluga ukryta w Torze — zdalny SSH bez publicznego adresu IP.
  # Operator trzyma nas za CGNAT (WAN 100.74.x.x, maska /32), wiec nie ma
  # czego przekierowac. Tor odwraca kierunek: serwer sam wychodzi na
  # zewnatrz i rejestruje adres .onion.
  services.tor = {
    enable = true;

    # Sam klient nie jest potrzebny — serwer niczego nie przeglada.
    client.enable = false;

    relay.onionServices.ssh = {
      version = 3;
      map = [
        {
          port = 22;
          target = {
            addr = "127.0.0.1";
            port = 22;
          };
        }
      ];
    };
  };
}
