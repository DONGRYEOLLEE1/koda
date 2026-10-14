const BOOKS = [
  { tone: "tone-1", title: "밤의 도서관", author: "김하늘", price: "14,000원" },
  { tone: "tone-2", title: "느린 바다", author: "박서윤", price: "16,500원" },
  { tone: "tone-3", title: "정원사의 수첩", author: "이도현", price: "13,200원" },
  { tone: "tone-4", title: "작은 기계들", author: "최유진", price: "18,000원" },
];

function makeCard(book) {
  const node = document.getElementById("card-tpl").content.firstElementChild.cloneNode(true);
  node.querySelector(".cover").classList.add(book.tone);
  node.querySelector(".title").textContent = book.title;
  node.querySelector(".author").textContent = book.author;
  node.querySelector(".price").textContent = book.price;
  return node;
}

const root = document.getElementById("cards");
for (const book of BOOKS) root.appendChild(makeCard(book));
