from oven import DemoOven


class DemoSource:
    def __init__(self, params=None):
        self.oven = DemoOven(params)
        self.finished = False
        self.total_min = self.oven.total_seconds() / 60

    def read_batch(self, steps):
        rows = []
        for _ in range(steps):
            if self.oven.t >= self.oven.total_seconds():
                self.finished = True
                break
            self.oven.step()
            rows.append(self.oven.read())
        return rows
