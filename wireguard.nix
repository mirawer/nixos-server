{ ... }:

{
  # Tunel VPN do zdalnego dostępu. Ruch dzielony: klient wysyła tunelem
  # tylko 10.100.0.0/24, cala reszta jego internetu idzie normalnie.
  networking.wireguard.interfaces.wg0 = {
    ips = [ "10.100.0.1/24" ];
    listenPort = 51820;

    # Klucz prywatny nigdy nie trafia do repo — tylko sciezka do niego.
    privateKeyFile = "/var/lib/secrets/wg-server-private";

    peers = [
      {
        # laptop "echo"
        publicKey = "ADqGgbdk6BwEhrkZ2w8WbirF5Lhs2HN8KahVysDcARw=";
        allowedIPs = [ "10.100.0.2/32" ];
      }
    ];
  };

  networking.firewall.allowedUDPPorts = [ 51820 ];
}
