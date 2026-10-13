// Date labels in the page language. Many browsers ship no Kazakh calendar data and
// would print "M10 13", so Kazakh is formatted by hand; other languages use Intl.
(() => {
  const months = ['қаңтар', 'ақпан', 'наурыз', 'сәуір', 'мамыр', 'маусым', 'шілде', 'тамыз', 'қыркүйек', 'қазан', 'қараша', 'желтоқсан'];
  const shortMonths = ['қаң.', 'ақп.', 'нау.', 'сәу.', 'мам.', 'мау.', 'шіл.', 'там.', 'қыр.', 'қаз.', 'қар.', 'жел.'];
  const weekdays = ['жексенбі', 'дүйсенбі', 'сейсенбі', 'сәрсенбі', 'бейсенбі', 'жұма', 'сенбі'];
  const shortWeekdays = ['жс', 'дс', 'сс', 'ср', 'бс', 'жм', 'сб'];
  const lang = document.documentElement.lang;
  const locale = ({ ru: 'ru-RU', kk: 'kk-KZ' })[lang] || 'en-GB';

  // style: 'date' → 13 қаз. 2026 · 'long' → 13 қазан, сейсенбі · 'chip' → жм, 16 қаз.
  window.formatStudyDate = (date, style) => {
    if (lang === 'kk') {
      const day = date.getDate(), month = date.getMonth(), weekday = date.getDay();
      if (style === 'long') return day + ' ' + months[month] + ', ' + weekdays[weekday];
      if (style === 'chip') return shortWeekdays[weekday] + ', ' + day + ' ' + shortMonths[month];
      return day + ' ' + shortMonths[month] + ' ' + date.getFullYear();
    }
    const options = style === 'long' ? { weekday: 'long', day: 'numeric', month: 'long' }
      : style === 'chip' ? { weekday: 'short', day: 'numeric', month: 'short' }
      : { year: 'numeric', month: 'short', day: 'numeric' };
    return new Intl.DateTimeFormat(locale, options).format(date);
  };
})();
