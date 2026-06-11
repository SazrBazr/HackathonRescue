class SignalDenoiseEngine:
    def __init__(self, smoothing_factor: float = 0.3):
        """
        smoothing_factor (alpha): Value between 0.0 and 1.0.
        A lower value (e.g., 0.3) heavily weights historical states to suppress noise.
        A higher value (e.g., 0.8) reacts faster to rapid physical movement.
        """
        self.alpha = smoothing_factor
        self.history = {}

    def clean_radius(self, victim_id: str, raw_radius: float) -> float:
        # If this is the first packet from a beacon, initialize historical tracking
        if victim_id not in self.history:
            self.history[victim_id] = raw_radius
            return round(raw_radius, 2)

        # Exponential Moving Average Formula
        old_radius = self.history[victim_id]
        smoothed_radius = (self.alpha * raw_radius) + ((1.0 - self.alpha) * old_radius)

        # Stash the updated state for the next incoming packet iteration
        self.history[victim_id] = smoothed_radius

        return round(smoothed_radius, 2)