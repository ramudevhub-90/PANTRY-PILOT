// A friendly emoji for a grocery name: the pantry's own emoji when the item is stocked there,
// otherwise a keyword match, otherwise a basket.

const KEYWORDS: [RegExp, string][] = [
  [/milk|curd|dahi|buttermilk|lassi/, '🥛'],
  [/yog(h)?urt/, '🥣'],
  [/paneer|cheese|cheddar|parmesan|mozzarella/, '🧀'],
  [/butter|ghee/, '🧈'],
  [/egg/, '🥚'],
  [/bread|pav|bun/, '🍞'],
  [/rice|poha|idli|dosa batter/, '🍚'],
  [/atta|flour|maida|besan|sooji|rava|semolina/, '🌾'],
  [/dal|lentil|chana|rajma|moong|toor|urad/, '🫘'],
  [/pasta|penne|spaghetti|noodle/, '🍝'],
  [/oil/, '🫗'],
  [/salt|sugar|jaggery/, '🧂'],
  [/tea|chai/, '🍵'],
  [/coffee/, '☕'],
  [/chicken/, '🍗'],
  [/mutton|lamb|meat/, '🥩'],
  [/fish|prawn|shrimp/, '🐟'],
  [/onion/, '🧅'],
  [/garlic/, '🧄'],
  [/ginger/, '🫚'],
  [/potato|aloo/, '🥔'],
  [/tomato/, '🍅'],
  [/carrot/, '🥕'],
  [/spinach|palak|methi|greens|lettuce|cabbage/, '🥬'],
  [/coriander|cilantro|mint|curry leaves|basil/, '🌿'],
  [/chilli|chili|pepper|capsicum/, '🌶️'],
  [/cucumber/, '🥒'],
  [/brinjal|eggplant|baingan/, '🍆'],
  [/corn/, '🌽'],
  [/mushroom/, '🍄'],
  [/peas|beans/, '🫛'],
  [/lemon|lime/, '🍋'],
  [/banana/, '🍌'],
  [/apple/, '🍎'],
  [/mango/, '🥭'],
  [/orange/, '🍊'],
  [/grape/, '🍇'],
  [/coconut/, '🥥'],
  [/water/, '💧'],
  [/juice/, '🧃'],
  [/biscuit|cookie/, '🍪'],
  [/chips|namkeen/, '🥨'],
  [/chocolate/, '🍫'],
  [/soap|detergent|shampoo/, '🧴'],
];

export function foodEmoji(name: string, pantry: { name: string; emoji: string }[] = []): string {
  const key = name.trim().toLowerCase();
  const stocked = pantry.find((p) => p.name.toLowerCase() === key || p.name.toLowerCase().includes(key));
  if (stocked?.emoji) return stocked.emoji;
  return KEYWORDS.find(([pattern]) => pattern.test(key))?.[1] ?? '🛒';
}
