const BOOKS = [
  { tone: "tone-1", title: "밤의 도서관", author: "김하늘", price: "14,000원" },
  { tone: "tone-2", title: "느린 바다", author: "박서윤", price: "16,500원" },
  { tone: "tone-3", title: "정원사의 수첩", author: "이도현", price: "13,200원" },
  { tone: "tone-4", title: "작은 기계들", author: "최유진", price: "18,000원" },
];

function cardHtml(book) {
  return `
    <article class="card">
      <div class="cover ${book.tone}" aria-hidden="true"></div>
      <h2 class="title">${book.title}</h2>
      <p class="author">${book.author}</p>
      <p class="price">${book.price}</p>
      <button type="button" class="buy">장바구니 담기</button>
    </article>`;
}

document.getElementById("cards").innerHTML = BOOKS.map(cardHtml).join("");
