@echo off
rem FlightQA command-line wrapper. The real executable flightqa-cli.exe sits in the parent folder (hidden).
rem Usage: tools\flightqa-cli.cmd run <folder> [--mission mission.kmz] [--pdf] ...
"%~dp0..\flightqa-cli.exe" %*
