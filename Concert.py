from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Concert:
    artist_id: str
    venue: str
    city: str
    start_date: str
    end_date: str | None
    is_sold_out: bool
    address: str | None = None
    lat: float | None = None
    lng: float | None = None
    distance: float | None = None
    distance_is_estimated: bool = False
    is_drivable: bool = True
    navigation_error: str | None = None
    ticket_url: str | None = None

    @classmethod
    def from_seated_event(
        cls,
        artist_id: str,
        attributes: dict[str, Any],
        *,
        event_id: str | None = None,
    ) -> "Concert":
        ticket_url = (
            attributes.get("exchange-listing-url")
            or (f"https://go.seated.com/tour-events/{event_id}" if event_id else None)
            or attributes.get("vip-link-url")
        )
        return cls(
            artist_id=artist_id,
            venue=attributes.get("venue-name", ""),
            city=attributes.get("formatted-address", ""),
            start_date=attributes.get("starts-at-date-local", ""),
            end_date=attributes.get("ends-at-date-local"),
            is_sold_out=attributes.get("is-sold-out", False),
            ticket_url=ticket_url,
        )

    @classmethod
    def from_normalized_event(cls, provider: str, attributes: dict[str, Any]) -> "Concert":
        return cls(
            artist_id=provider,
            venue=attributes.get("venue", ""),
            city=attributes.get("city", ""),
            start_date=attributes.get("start_date", ""),
            end_date=attributes.get("end_date"),
            is_sold_out=attributes.get("is_sold_out", False),
            ticket_url=attributes.get("ticket_url"),
            lat=attributes.get("lat"),
            lng=attributes.get("lng"),
        )

    def mark_navigation_error(self, status: str) -> None:
        self.is_drivable = False
        self.navigation_error = status

    def print_info(self) -> None:
        print()
        print(self)

        if self.is_sold_out:
            print("**SOLD OUT**")
            return

        if not self.is_drivable:
            print("**not drivable**")
            if self.navigation_error:
                print(self.navigation_error)
            return

        if self.distance is None:
            print("Distance unavailable")
        else:
            print(f"{self.distance} miles")

    def __str__(self) -> str:
        return f"{self.venue}\n{self.city}\n{self.start_date}"
