"""Quick-add parsing: one line becomes a task, in Russian or English."""
import unittest
from datetime import date

from quick_add import parse

MONDAY = date(2026, 10, 12)


class QuickAddTests(unittest.TestCase):
    def check(self, text, **expected):
        result = parse(text, MONDAY, "Общее")
        for key, value in expected.items():
            self.assertEqual(result[key], value, (text, key, result))
        return result

    def test_full_russian_line(self):
        result = self.check("Эссе по философии пт 18:00 !высокий #философия",
                            title="Эссе по философии", course="философия", due_date="2026-10-16",
                            priority="High", notes="18:00")
        self.assertEqual(set(result["found"]), {"due_date", "course", "priority", "time"})

    def test_english_and_relative_dates(self):
        self.check("Read chapter 4 tomorrow !low #math", title="Read chapter 4", due_date="2026-10-13", priority="Low", course="math")
        self.check("Lab report in 3 days", due_date="2026-10-15", title="Lab report")
        self.check("Конспект через 10 дней", due_date="2026-10-22", title="Конспект")
        self.check("Слова к тесту послезавтра", due_date="2026-10-14")

    def test_weekdays_include_today_and_wrap(self):
        self.check("Встреча в понедельник", due_date="2026-10-12", title="Встреча")
        self.check("Проект вс", due_date="2026-10-18")
        self.check("Gym on friday", due_date="2026-10-16", title="Gym")

    def test_numeric_dates(self):
        self.check("Курсовая 25.10", due_date="2026-10-25", title="Курсовая")
        self.check("Сессия 15.01", due_date="2027-01-15")
        self.check("Экзамен 2026-12-20", due_date="2026-12-20")
        self.check("Ошибка 31.02", due_date="2026-10-12", title="Ошибка 31.02")

    def test_defaults_and_plain_text(self):
        result = self.check("Купить тетради", title="Купить тетради", course="Общее", due_date="2026-10-12", priority="Medium", notes="")
        self.assertEqual(result["found"], {})
        self.check("Data_Structures homework #data_structures !!!", course="data structures", priority="High")
        self.check("Почему 2+2! важно", title="Почему 2+2! важно")

    def test_kazakh_line(self):
        self.check("Эссе жұма 18:00 !жоғары #философия", title="Эссе", due_date="2026-10-16", priority="High", course="философия", notes="18:00")
        self.check("Конспект ертең !төмен", due_date="2026-10-13", priority="Low", title="Конспект")
        self.check("Жоба бүрсігүні", due_date="2026-10-14", title="Жоба")
        self.check("Емтихан 5 күннен кейін #математика", due_date="2026-10-17", course="математика", title="Емтихан")
        self.check("Сабақ дс", due_date="2026-10-12", title="Сабақ")
        self.check("Қазақ әдебиеті жс !орташа", title="Қазақ әдебиеті", due_date="2026-10-18", priority="Medium")

    def test_only_first_of_each_kind_is_used(self):
        self.check("Сдать завтра или пт #a #b", due_date="2026-10-13", course="a", title="Сдать или пт #b")


if __name__ == "__main__":
    unittest.main()
