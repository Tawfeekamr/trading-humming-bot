use hmac::{Hmac, Mac};
use sha2::Sha256;

type HmacSha256 = Hmac<Sha256>;

#[test]
fn test_binance_signature_matches_expected() {
    let secret = "NhqPtmdSJYdKjVHjA7PZj4Mge3R5YNiP1e3UZjInClVN65XAbvqqM6A7H5fATj0j";
    let query_string = "symbol=LTCBTC&side=BUY&type=LIMIT&timeInForce=GTC&quantity=1&price=0.1&recvWindow=5000&timestamp=1499827319559";

    println!("Query string: {}", query_string);

    let mut mac = HmacSha256::new_from_slice(secret.as_bytes()).expect("HMAC can take key of any size");
    mac.update(query_string.as_bytes());
    let result = mac.finalize();
    let signature = hex::encode(result.into_bytes());

    println!("Generated signature: {}", signature);
    println!("Expected signature:  c8db56825ae71d6d79447849e617115f4a920fa2acdcab2b053c4b5737c4880b");

    assert_eq!(
        signature,
        "c8db56825ae71d6d79447849e617115f4a920fa2acdcab2b053c4b2838bd6b71"
    );
}
