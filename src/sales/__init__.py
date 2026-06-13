"""Governed sales-conversation subsystem.

A deterministic phase engine drives sales conversations (opener -> qualify ->
close -> book), bot phrasing is governed by the LLM swarm, and meeting bookings
go through the capability broker (Calendly) so every booking is policy-gated and
recorded in the capability audit trail.
"""
