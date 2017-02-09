[![Build Status](https://travis-ci.org/Noctem/pgoapi.svg?branch=async)](https://travis-ci.org/Noctem/pgoapi)

# pgoapi - a pokemon go api lib in python
pgoapi is a client/api/demo for Pokemon Go by https://github.com/tejado.  
It allows automatic parsing of requests/responses by finding the correct protobuf objects over a naming convention and will return the response in a parsed python dictionary format.   

 * This is unofficial - USE AT YOUR OWN RISK!
 * No bot/farming code included!

## Feature Support
 * Python 3
 * Google/PTC auth
 * Address parsing for GPS coordinates
 * Allows chaining of RPC calls
 * Re-auth if ticket expired
 * Check for server side-throttling
 * Thread-safety
 * Advanced logging/debugging
 * Uses [POGOProtos](https://github.com/Noctem/POGOProtos)
 * Mostly all available RPC calls (see [API reference](https://docs.pogodev.org) on the wiki)

## Documentation
Documentation is available at the [pgoapi wiki](https://wiki.pogodev.org).

## Requirements
 * Python ≥3.5
 * aiohttp
 * protobuf (≥3)
 * gpsoauth
 * s2sphere

## Contribution
Contributions are highly welcome. Please use github or [Discord](https://discord.pogodev.org) for it!

## Credits
[Mila432](https://github.com/Mila432/Pokemon_Go_API) for the login secrets  
[elliottcarlson](https://github.com/elliottcarlson) for the Google Auth PR  
[AeonLucid](https://github.com/AeonLucid/POGOProtos) for improved protos  
[AHAAAAAAA](https://github.com/AHAAAAAAA/PokemonGo-Map) for parts of the s2sphere stuff  
[DeirhX](https://github.com/DeirhX) for thread-safety
