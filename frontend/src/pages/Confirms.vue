<template>
  <div>
    <div class="hero"><div><h1>回执</h1><p>供应商回执数量，三联对齐第三腿。同供应商+货号改挂锚日闭包；锚日封死后回执进隔离区。</p></div><button @click="load">刷新</button></div>
    <div class="panel">
      <div class="row">
        <input v-model="form.confirm_no" placeholder="回执号" />
        <input v-model="form.supplier_code" placeholder="供应商编码" />
        <input v-model="form.sku" placeholder="货号" />
        <input v-model="form.dock_day" placeholder="到货日" />
        <input v-model.number="form.qty" type="number" placeholder="数量" />
        <button @click="add">登记回执</button>
      </div>
      <p class="msg">{{ err }}</p>
      <p class="okmsg">{{ ok }}</p>
      <table>
        <thead><tr><th>回执号</th><th>供应商</th><th>货号</th><th>锚日闭包</th><th>数量</th></tr></thead>
        <tbody>
          <tr v-for="r in rows" :key="r.id"><td>{{ r.confirm_no }}</td><td>{{ r.supplier_code }}</td><td>{{ r.sku }}</td><td>{{ r.dock_day }}</td><td>{{ r.qty }}</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api'
const rows = ref([])
const err = ref('')
const ok = ref('')
const form = ref({ confirm_no: '', supplier_code: 'SUP-A', sku: '', dock_day: '2026-11-18', qty: 1 })
async function load() { rows.value = await api('/confirms') }
async function add() {
  err.value = ''; ok.value = ''
  try {
    const res = await api('/confirms', { method: 'POST', body: JSON.stringify(form.value) })
    ok.value = res.folded ? `已改挂至锚日 ${res.anchor_day}` : `已落锚日 ${res.anchor_day}`
    await load()
  } catch (e) { err.value = e.message }
}
onMounted(load)
</script>
