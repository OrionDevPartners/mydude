"""Distributed power-burst fabric for MyDude fleet compute scaling.

Public API (everything else is implementation detail):
  from src.fleet.burst.manager import get_burst_manager, measure_saturation
  from src.fleet.burst.registry import first_configured_backend
  from src.fleet.burst.interface import BurstBackend, BurstWorkerHandle
"""
